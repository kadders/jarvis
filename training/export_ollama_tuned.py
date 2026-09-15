#!/usr/bin/env python3
"""
Merge a DPO/QLoRA PEFT adapter into the base HF model and export GGUF + Modelfile
for `ollama create`.

Typical cluster usage (adapters PVC mounted at /data/adapters):

  python -m training.export_ollama_tuned \\
    --run-dir /data/adapters/run_rrf_nxt \\
    --base-model unsloth/Llama-3.2-3B-Instruct \\
    --outtype q8_0 \\
    --tag jarvis-tuned:latest
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _merge_adapter(
    *,
    base_model: str,
    adapter_dir: Path,
    merged_dir: Path,
) -> None:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"loading base {base_model!r} (cpu float16)…", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    base = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.float16,
        device_map="cpu",
        low_cpu_mem_usage=True,
        trust_remote_code=True,
    )
    print(f"loading adapter {adapter_dir}…", flush=True)
    model = PeftModel.from_pretrained(base, str(adapter_dir))
    print("merging LoRA…", flush=True)
    model = model.merge_and_unload()
    merged_dir.mkdir(parents=True, exist_ok=True)
    print(f"saving merged HF → {merged_dir}", flush=True)
    model.save_pretrained(str(merged_dir), safe_serialization=True)
    tokenizer.save_pretrained(str(merged_dir))
    del model, base
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _ensure_convert_deps() -> None:
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "gguf>=0.10.0",
            "protobuf",
            "sentencepiece",
        ],
    )


def _ensure_llama_cpp_convert(work_dir: Path) -> Path:
    """Shallow-clone llama.cpp if needed; return path to convert_hf_to_gguf.py."""
    repo = work_dir / "llama.cpp"
    script = repo / "convert_hf_to_gguf.py"
    if not script.is_file():
        print(f"cloning llama.cpp into {repo}…", flush=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        if repo.exists():
            shutil.rmtree(repo)
        subprocess.check_call(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "https://github.com/ggerganov/llama.cpp.git",
                str(repo),
            ],
        )
    if not script.is_file():
        raise FileNotFoundError(f"convert script missing after clone: {script}")
    _ensure_convert_deps()
    return script


def _convert_gguf(
    *,
    merged_dir: Path,
    gguf_path: Path,
    outtype: str,
    work_dir: Path,
) -> None:
    script = _ensure_llama_cpp_convert(work_dir)
    gguf_path.parent.mkdir(parents=True, exist_ok=True)
    if gguf_path.exists():
        gguf_path.unlink()
    cmd = [
        sys.executable,
        str(script),
        str(merged_dir),
        "--outfile",
        str(gguf_path),
        "--outtype",
        outtype,
    ]
    print("running:", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=str(script.parent))
    if not gguf_path.is_file():
        raise FileNotFoundError(f"GGUF not written: {gguf_path}")
    size_mb = gguf_path.stat().st_size / (1024 * 1024)
    print(f"wrote {gguf_path} ({size_mb:.1f} MiB)", flush=True)


def write_modelfile(
    out_dir: Path,
    *,
    gguf_name: str,
    tag: str,
) -> Path:
    """Modelfile with FROM pointing at a sibling GGUF (for ollama create cwd)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "Modelfile"
    body = f"""# Jarvis DPO/QLoRA serve recipe
# From this directory on the Ollama pod:
#   ollama create {tag} -f Modelfile
FROM ./{gguf_name}
PARAMETER temperature 0.7
SYSTEM You are Jarvis. Prefer corrected answers from operator feedback. For RepRapFirmware / NeXT (MOS-nxt) macros, follow RRF meta and echo/abort conventions from training.
"""
    path.write_text(body, encoding="utf-8")
    return path


def export(
    *,
    run_dir: Path,
    base_model: str,
    adapter_subdir: str,
    outtype: str,
    tag: str,
    skip_merge: bool,
    skip_gguf: bool,
) -> Path:
    adapter_dir = run_dir / adapter_subdir
    if not adapter_dir.is_dir():
        raise SystemExit(f"adapter dir missing: {adapter_dir}")

    merged_dir = run_dir / "merged"
    if skip_merge:
        if not (merged_dir / "config.json").is_file():
            raise SystemExit(f"--skip-merge but merged model missing at {merged_dir}")
        print(f"reusing merged HF at {merged_dir}", flush=True)
    else:
        _merge_adapter(
            base_model=base_model,
            adapter_dir=adapter_dir,
            merged_dir=merged_dir,
        )

    gguf_name = f"jarvis-tuned-{outtype}.gguf"
    gguf_path = run_dir / gguf_name
    if skip_gguf:
        if not gguf_path.is_file():
            raise SystemExit(f"--skip-gguf but file missing: {gguf_path}")
        print(f"reusing GGUF at {gguf_path}", flush=True)
    else:
        _convert_gguf(
            merged_dir=merged_dir,
            gguf_path=gguf_path,
            outtype=outtype,
            work_dir=run_dir / ".export_work",
        )

    modelfile = write_modelfile(run_dir, gguf_name=gguf_name, tag=tag)
    meta = {
        "tag": tag,
        "base_model": base_model,
        "adapter_dir": str(adapter_dir),
        "merged_dir": str(merged_dir),
        "gguf": str(gguf_path),
        "modelfile": str(modelfile),
        "outtype": outtype,
    }
    meta_path = run_dir / "export_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2), flush=True)
    return gguf_path


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--run-dir",
        type=Path,
        default=Path(os.environ.get("JARVIS_ADAPTER_RUN_DIR", "cache/llm_adapters/run")),
        help="Train output dir containing adapter/ (default: JARVIS_ADAPTER_RUN_DIR or cache/…)",
    )
    p.add_argument(
        "--base-model",
        default=os.environ.get("JARVIS_BASE_MODEL", "unsloth/Llama-3.2-3B-Instruct"),
    )
    p.add_argument("--adapter-subdir", default="adapter")
    p.add_argument(
        "--outtype",
        default="q8_0",
        choices=("f16", "bf16", "q8_0", "q4_0", "q4_1"),
        help="GGUF quant type supported by convert_hf_to_gguf without llama-quantize",
    )
    p.add_argument("--tag", default="jarvis-tuned:latest")
    p.add_argument("--skip-merge", action="store_true")
    p.add_argument("--skip-gguf", action="store_true")
    args = p.parse_args(argv)

    export(
        run_dir=args.run_dir.resolve(),
        base_model=args.base_model,
        adapter_subdir=args.adapter_subdir,
        outtype=args.outtype,
        tag=args.tag,
        skip_merge=args.skip_merge,
        skip_gguf=args.skip_gguf,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
