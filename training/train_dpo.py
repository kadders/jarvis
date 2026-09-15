#!/usr/bin/env python3
"""
QLoRA + DPO training entrypoint for Jarvis preference pairs.

Designed for Pascal 8 GB (GTX 1070) with aggressive quantization. Requires optional
deps: torch, transformers, peft, trl, datasets, bitsandbytes (see
training/requirements-train.txt).

Without GPU / train deps, use --dry-run to validate the dataset and write a
Modelfile stub for Ollama import after an offline train elsewhere.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def load_pairs(path: Path) -> List[Dict[str, str]]:
    pairs: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if not all(k in rec for k in ("prompt", "chosen", "rejected")):
                continue
            pairs.append(
                {
                    "prompt": rec["prompt"],
                    "chosen": rec["chosen"],
                    "rejected": rec["rejected"],
                }
            )
    return pairs


def write_modelfile(out_dir: Path, *, base_model: str, tag: str) -> Path:
    """
    Write an Ollama Modelfile that FROM the base tag.

    After exporting a GGUF (or merging LoRA into a full model and converting),
    replace FROM with the local GGUF path or `ollama create` from a custom blob.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "Modelfile"
    body = f"""# Jarvis DPO/QLoRA serve recipe
# Build (after placing weights): ollama create {tag} -f Modelfile
FROM {base_model}
PARAMETER temperature 0.7
SYSTEM You are Jarvis. Prefer corrected answers from operator feedback.
"""
    path.write_text(body, encoding="utf-8")
    meta = {
        "tag": tag,
        "base_model": base_model,
        "modelfile": str(path),
        "note": "Replace FROM with merged GGUF path after train, then ollama create",
    }
    (out_dir / "train_meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return path


def run_dpo(
    pairs: List[Dict[str, str]],
    *,
    base_model: str,
    out_dir: Path,
    max_steps: int,
    batch_size: int,
) -> None:
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from trl import DPOConfig, DPOTrainer
        # NOTE: trl's DPOTrainer calls PEFT's prepare_model_for_kbit_training(), which
        # (in peft 0.13.x) can cast many float16/bfloat16 params to fp32. On 8GB GPUs
        # this can OOM before training even starts. We monkeypatch the function inside
        # trl to skip the fp32 cast to make this PoC fit on GTX 1070-class hardware.
        import trl.trainer.dpo_trainer as dpo_trainer_mod
        import inspect as _inspect
        from transformers import Trainer as _HFTrainer

        # trl's DPOTrainer defines a get_batch_samples(model, batch) method for generation,
        # but transformers' Trainer._inner_training_loop calls get_batch_samples(epoch_iterator, num_batches).
        # With our current transformers/trl combo, this name collision causes the training loop
        # to pass the epoch iterator as "model", leading to:
        #   AttributeError: 'generator' object has no attribute 'generate'
        # Patch DPOTrainer.get_batch_samples to dispatch based on whether the first argument
        # looks like a model (has .generate) or an epoch iterator.
        _orig_gen_get_batch_samples = dpo_trainer_mod.DPOTrainer.get_batch_samples

        def _patched_get_batch_samples(self, arg1, arg2):
            if hasattr(arg1, "generate"):
                return _orig_gen_get_batch_samples(self, arg1, arg2)
            return _HFTrainer.get_batch_samples(self, arg1, arg2)

        dpo_trainer_mod.DPOTrainer.get_batch_samples = _patched_get_batch_samples
        _orig_compute_loss = dpo_trainer_mod.DPOTrainer.compute_loss

        def _patched_compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            # transformers Trainer.training_step passes num_items_in_batch=...
            # Some trl versions don't accept it.
            return _orig_compute_loss(self, model, inputs, return_outputs=return_outputs)

        dpo_trainer_mod.DPOTrainer.compute_loss = _patched_compute_loss

        # transformers Trainer calls self.log(logs, start_time). Some trl versions
        # define log(self, logs) only; patch it to accept the extra arg.
        _orig_log = dpo_trainer_mod.DPOTrainer.log

        def _patched_log(self, logs, *args, **kwargs):
            return _orig_log(self, logs)

        dpo_trainer_mod.DPOTrainer.log = _patched_log

        def _patched_prepare_model_for_kbit_training(
            model,
            use_gradient_checkpointing: bool = True,
            gradient_checkpointing_kwargs: dict | None = None,
        ):
            loaded_in_kbit = (
                getattr(model, "is_loaded_in_8bit", False)
                or getattr(model, "is_loaded_in_4bit", False)
            )
            is_gptq_quantized = getattr(model, "quantization_method", None) == "gptq"
            is_aqlm_quantized = getattr(model, "quantization_method", None) == "aqlm"
            is_eetq_quantized = getattr(model, "quantization_method", None) == "eetq"
            is_hqq_quantized = getattr(model, "quantization_method", None) == "hqq" or getattr(
                model, "hqq_quantized", False
            )

            if gradient_checkpointing_kwargs is None:
                gradient_checkpointing_kwargs = {}

            # Freeze base model's layers
            for _, param in model.named_parameters():
                param.requires_grad = False

            # Skip the original fp16/bf16 → fp32 casting block (the OOM source).

            if (
                loaded_in_kbit
                or is_gptq_quantized
                or is_aqlm_quantized
                or is_eetq_quantized
                or is_hqq_quantized
            ) and use_gradient_checkpointing:
                if "use_reentrant" not in gradient_checkpointing_kwargs or gradient_checkpointing_kwargs.get(
                    "use_reentrant"
                ):
                    if hasattr(model, "enable_input_require_grads"):
                        model.enable_input_require_grads()
                    else:

                        def make_inputs_require_grad(module, input, output):
                            output.requires_grad_(True)

                        model.get_input_embeddings().register_forward_hook(make_inputs_require_grad)

                _supports_gc_kwargs = (
                    "gradient_checkpointing_kwargs"
                    in list(_inspect.signature(model.gradient_checkpointing_enable).parameters)
                )
                if not _supports_gc_kwargs:
                    gradient_checkpointing_kwargs = {}

                gc_enable_kwargs = (
                    {}
                    if not _supports_gc_kwargs
                    else {"gradient_checkpointing_kwargs": gradient_checkpointing_kwargs}
                )
                model.gradient_checkpointing_enable(**gc_enable_kwargs)
            return model

        dpo_trainer_mod.prepare_model_for_kbit_training = _patched_prepare_model_for_kbit_training
    except ImportError as e:
        raise SystemExit(
            f"Missing training dependencies ({e}). Install training/requirements-train.txt "
            "or pass --dry-run."
        ) from e

    if not torch.cuda.is_available():
        raise SystemExit("CUDA required for QLoRA/DPO on this script (or use --dry-run)")

    out_dir.mkdir(parents=True, exist_ok=True)
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    # On 8GB (GTX 1070-class) GPUs, QLoRA can still OOM during PEFT prep
    # (k-bit → fp32 layer norm conversion). Allow transformers to offload
    # some modules to CPU by tightening max_memory.
    max_memory = None
    if torch.cuda.is_available():
        total_gib = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        # Keep ~total-1GiB for safety, but don't go below 4GiB.
        keep_gib = max(4, int(total_gib - 1))
        max_memory = {0: f"{keep_gib}GiB", "cpu": "48GiB"}

    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        quantization_config=bnb,
        device_map="auto",
        max_memory=max_memory,
        low_cpu_mem_usage=True,
        trust_remote_code=True,
    )
    peft_config = LoraConfig(
        r=8,
        lora_alpha=16,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
    )
    ds = Dataset.from_list(
        [
            {
                "prompt": p["prompt"],
                "chosen": p["chosen"],
                "rejected": p["rejected"],
            }
            for p in pairs
        ]
    )
    args = DPOConfig(
        output_dir=str(out_dir / "checkpoints"),
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=max(1, 8 // max(1, batch_size)),
        learning_rate=5e-5,
        max_steps=max_steps,
        logging_steps=1,
        save_steps=max(max_steps, 1),
        bf16=False,
        fp16=True,
        remove_unused_columns=False,
        report_to=[],
        # Default DPO lengths (max_length=512, max_prompt_length=128) are too
        # memory-hungry on 8GB GPUs for the 8B model. Keep them small for the PoC.
        max_length=256,
        max_prompt_length=64,
    )
    print(f"DEBUG base model type={type(model)} has_generate={hasattr(model,'generate')}")
    trainer = DPOTrainer(
        model=model,
        args=args,
        train_dataset=ds,
        tokenizer=tokenizer,
        peft_config=peft_config,
    )
    print(f"DEBUG trainer.model type={type(trainer.model)} has_generate={hasattr(trainer.model,'generate')}")
    trainer.train()
    adapter_dir = out_dir / "adapter"
    trainer.save_model(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    print(f"saved adapter → {adapter_dir}")


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--preferences",
        type=Path,
        default=Path("cache/llm_adapters/preferences.jsonl"),
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("cache/llm_adapters/run"),
    )
    p.add_argument(
        "--base-model",
        default="meta-llama/Llama-3.1-8B-Instruct",
        help="HF id matching the served Ollama family when possible",
    )
    p.add_argument("--ollama-base-tag", default="llama3.1:8b")
    p.add_argument("--ollama-tag", default="jarvis-tuned:latest")
    p.add_argument("--max-steps", type=int, default=50)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--min-pairs", type=int, default=8)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate pairs + write Modelfile only (no torch train)",
    )
    args = p.parse_args(argv)

    if not args.preferences.is_file():
        print(f"preferences not found: {args.preferences}", file=sys.stderr)
        return 2
    pairs = load_pairs(args.preferences)
    if len(pairs) < args.min_pairs:
        print(
            f"need >= {args.min_pairs} pairs, got {len(pairs)}",
            file=sys.stderr,
        )
        return 3

    write_modelfile(
        args.out_dir,
        base_model=args.ollama_base_tag,
        tag=args.ollama_tag,
    )
    (args.out_dir / "preferences.snapshot.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in pairs) + "\n",
        encoding="utf-8",
    )
    print(f"pairs={len(pairs)} out_dir={args.out_dir}")

    if args.dry_run:
        print("dry-run complete (Modelfile written; skipped GPU train)")
        return 0

    run_dpo(
        pairs,
        base_model=args.base_model,
        out_dir=args.out_dir,
        max_steps=args.max_steps,
        batch_size=args.batch_size,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
