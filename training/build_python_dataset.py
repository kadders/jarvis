#!/usr/bin/env python3
"""
Build a Python / Jarvis coding preference dataset.

Sources:
  - training/datasets/python_jarvis/STYLE.md
  - Jarvis api/, utils/, jarvis_mcp/, tap_collector/
  - Sibling tap-testing/tap_testing/ + docs
  - Sibling tiny-ocr/client/tiny_ocr_client/

Writes preferences.jsonl + instructions.jsonl for training.train_dpo.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from training.dataset_common import (
    Instruction,
    Preference,
    dedupe_prefs,
    extract_code_md_pairs,
    make_fence_re,
    write_jsonl,
)

LICENSE = "proprietary"
PYTHON_FENCE = make_fence_re("python")


def _black_format(source: str) -> Optional[str]:
    try:
        import black
    except ImportError:
        return None
    try:
        mode = black.Mode(line_length=88, target_versions={black.TargetVersion.PY310})
        return black.format_str(source, mode=mode)
    except black.InvalidInput:
        return None


def _unblacken(source: str) -> str:
    """Cheap synthetic reject: remove trailing commas, tighten spacing."""
    out = re.sub(r",\s*\)", ")", source)
    out = re.sub(r"def (\w+)\(", r"def \1 (", out)
    out = re.sub(r'"([^"]*)"', r"'\1'", out)
    return out


def _bare_except_antipattern(source: str) -> str:
    return re.sub(r"except\s+Exception\s*:", "except:", source)


def _prompt_python(topic: str, _chosen: str) -> str:
    return (
        f"You are writing Python for the Jarvis ecosystem. "
        f"Produce the CORRECT form for: {topic}. "
        "Follow Black formatting (88-char lines) and Jarvis conventions."
    )


def extract_style_md(style_path: Path) -> Tuple[List[Preference], List[Instruction]]:
    text = style_path.read_text(encoding="utf-8", errors="replace")
    return extract_code_md_pairs(
        text,
        str(style_path.name),
        kind="python_style",
        license_=LICENSE,
        tags=["STYLE.md", "python"],
        fence_re=PYTHON_FENCE,
        prompt_fn=_prompt_python,
        synthetic_reject_fn=_unblacken,
    )


def extract_black_pairs(
    source_path: str,
    code: str,
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    formatted = _black_format(code)
    if not formatted or formatted.strip() == code.strip():
        return prefs, instr
    unblack = _unblacken(code)
    if unblack.strip() == formatted.strip():
        return prefs, instr
    prompt = (
        f"Format this Python snippet with Black (88 cols). Source: {source_path}\n"
        f"```python\n{code.strip()}\n```"
    )
    prefs.append(Preference(prompt=prompt, chosen=formatted, rejected=unblack))
    instr.append(
        Instruction(
            prompt=prompt,
            response=formatted,
            kind="black_format",
            source_path=source_path,
            license=LICENSE,
            tags=["black"],
        )
    )
    return prefs, instr


def _iter_py_files(root: Path, subdirs: Sequence[str]) -> List[Path]:
    out: List[Path] = []
    for sub in subdirs:
        base = root / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if any(part in p.parts for part in ("__pycache__", ".venv", "venv", "tests")):
                continue
            if p.name.startswith("test_"):
                continue
            out.append(p)
    return out


def extract_function_snippets(
    py_path: Path,
    root: Path,
    *,
    kind: str = "python_style",
    max_funcs: int = 8,
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    try:
        text = py_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return prefs, instr

    rel = str(py_path.relative_to(root))
    count = 0
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_") and node.name != "__init__":
            continue
        if not node.body:
            continue
        lines = text.splitlines()
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        snippet = "\n".join(lines[start:end]).strip()
        if len(snippet) < 40 or len(snippet) > 1200:
            continue
        p, i = extract_black_pairs(rel, snippet)
        prefs.extend(p)
        instr.extend(i)

        reject = _bare_except_antipattern(snippet)
        if reject != snippet:
            prompt = (
                f"Improve this Python function from {rel} ({node.name}). "
                "Use specific exceptions and Black formatting."
            )
            chosen = _black_format(snippet) or snippet
            prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=reject))
            instr.append(
                Instruction(
                    prompt=prompt,
                    response=chosen,
                    kind=kind,
                    source_path=rel,
                    license=LICENSE,
                    tags=["snippet", node.name],
                )
            )
        count += 1
        if count >= max_funcs:
            break
    return prefs, instr


def extract_mqtt_doc_pairs(docs_dir: Path) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    for name in ("MQTT_TELEMETRY.md", "MQTT_PAYLOAD_REFERENCE.md"):
        path = docs_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"`(tap/[^`]+)`", text):
            topic = m.group(1)
            prompt = f"What MQTT topic does tap-testing use for: {topic.split('/')[-1]}?"
            chosen = f"Publish/subscribe on `{topic}` (see tap-testing {name})."
            rejected = f"Use duet/{topic.split('/')[-1]} instead of tap/."
            prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
            instr.append(
                Instruction(
                    prompt=prompt,
                    response=chosen,
                    kind="mqtt_telemetry",
                    source_path=name,
                    license=LICENSE,
                    tags=["mqtt", "docs"],
                )
            )
    return prefs, instr


def extract_jarvis_convention_pairs() -> Tuple[List[Preference], List[Instruction]]:
    pairs = [
        (
            "Where should LLM logic live vs ASR in the Jarvis monorepo?",
            "LLM logic in root api/, models/, utils/; ASR only under asr/.",
            "Mix ASR WebSocket handlers into api/routers/events.py.",
            "jarvis_conventions",
        ),
        (
            "What header must POST /api/v1/events honor for safe retries?",
            "Idempotency-Key — backends memory or redis (REDIS_URL).",
            "Retry-After header only; no idempotency store needed.",
            "jarvis_conventions",
        ),
        (
            "Default Python formatter for Jarvis Python code?",
            "Black with line-length 88 and target-version py310.",
            "autopep8 with 79 columns.",
            "python_style",
        ),
    ]
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    for prompt, chosen, rejected, kind in pairs:
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind=kind,
                source_path="AGENTS.md",
                license=LICENSE,
                tags=["conventions"],
            )
        )
    return prefs, instr


def build_dataset(
    jarvis_root: Path,
    tap_root: Path,
    tiny_ocr_root: Path,
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []

    style = jarvis_root / "training" / "datasets" / "python_jarvis" / "STYLE.md"
    if style.is_file():
        p, i = extract_style_md(style)
        prefs.extend(p)
        instr.extend(i)

    p, i = extract_jarvis_convention_pairs()
    prefs.extend(p)
    instr.extend(i)

    jarvis_subdirs = ("api", "utils", "jarvis_mcp", "tap_collector")
    for py_path in _iter_py_files(jarvis_root, jarvis_subdirs)[:40]:
        p, i = extract_function_snippets(py_path, jarvis_root, kind="jarvis_conventions")
        prefs.extend(p)
        instr.extend(i)

    if tap_root.is_dir():
        for py_path in _iter_py_files(tap_root, ("tap_testing",))[:25]:
            p, i = extract_function_snippets(py_path, tap_root, kind="config_env")
            prefs.extend(p)
            instr.extend(i)
        docs = tap_root / "docs"
        if docs.is_dir():
            p, i = extract_mqtt_doc_pairs(docs)
            prefs.extend(p)
            instr.extend(i)

    if tiny_ocr_root.is_dir():
        client = tiny_ocr_root / "client" / "tiny_ocr_client"
        if client.is_dir():
            for py_path in sorted(client.glob("*.py"))[:10]:
                p, i = extract_function_snippets(py_path, tiny_ocr_root, kind="mcp_client")
                prefs.extend(p)
                instr.extend(i)

    prefs = dedupe_prefs(prefs)
    return prefs, instr


def write_readme(path: Path, *, n_prefs: int, n_instr: int) -> None:
    body = f"""# Python / Jarvis coding dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`).

| Field | Value |
|-------|--------|
| Preference rows | {n_prefs} |
| Instruction rows | {n_instr} |
| Formatter | Black 88 / py310 |

## Regenerate

```bash
python -m training.build_python_dataset \\
  --jarvis-root . \\
  --tap-testing-root ../tap-testing \\
  --tiny-ocr-root ../tiny-ocr \\
  --out-dir training/datasets/python_jarvis
```
"""
    path.write_text(body, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    here = Path(__file__).resolve().parent
    repo = here.parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jarvis-root", type=Path, default=repo)
    p.add_argument("--tap-testing-root", type=Path, default=repo.parent / "tap-testing")
    p.add_argument("--tiny-ocr-root", type=Path, default=repo.parent / "tiny-ocr")
    p.add_argument("--out-dir", type=Path, default=here / "datasets" / "python_jarvis")
    p.add_argument("--min-pairs", type=int, default=8)
    args = p.parse_args(argv)

    prefs, instr = build_dataset(
        args.jarvis_root.resolve(),
        args.tap_testing_root.resolve(),
        args.tiny_ocr_root.resolve(),
    )
    if len(prefs) < args.min_pairs:
        print(f"refusing to write: got {len(prefs)} pairs, need >= {args.min_pairs}", file=sys.stderr)
        return 3

    out_dir = args.out_dir
    n_p = write_jsonl(out_dir / "preferences.jsonl", (x.to_dict() for x in prefs))
    n_i = write_jsonl(out_dir / "instructions.jsonl", (x.to_dict() for x in instr))
    write_readme(out_dir / "README.md", n_prefs=n_p, n_instr=n_i)
    print(f"wrote {n_p} preferences, {n_i} instructions → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
