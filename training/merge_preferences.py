#!/usr/bin/env python3
"""Merge multiple preference JSONL files for DPO training."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

DOMAIN_TAGS = {
    "python_jarvis": "python_jarvis",
    "python_public": "python_public",
    "rrf_nxt": "rrf_nxt",
    "arduino_wand": "arduino_wand",
    "cad": "cad",
    "mentra": "mentra",
}


def _domain_for_path(path: Path) -> str:
    name = path.parent.name
    return DOMAIN_TAGS.get(name, "general")


def load_pairs(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            prompt = str(rec.get("prompt", "")).strip()
            chosen = str(rec.get("chosen", "")).strip()
            rejected = str(rec.get("rejected", "")).strip()
            if not prompt or not chosen or not rejected or chosen == rejected:
                continue
            rows.append({"prompt": prompt, "chosen": chosen, "rejected": rejected})
    return rows


def merge_files(
    inputs: List[Path],
    *,
    jarvis_ratio_min: float,
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    seen: Set[Tuple[str, str, str]] = set()
    merged: List[Dict[str, str]] = []
    counts: Dict[str, int] = {}

    buckets: Dict[str, List[Dict[str, str]]] = {}
    for path in inputs:
        domain = _domain_for_path(path)
        buckets.setdefault(domain, []).extend(load_pairs(path))
        counts[str(path)] = len(load_pairs(path))

    jarvis_py = len(buckets.get("python_jarvis", []))
    public_py = len(buckets.get("python_public", []))
    py_total = jarvis_py + public_py
    if py_total and jarvis_py / py_total < jarvis_ratio_min and public_py:
        keep_public = int(jarvis_py * (1 - jarvis_ratio_min) / jarvis_ratio_min)
        buckets["python_public"] = buckets["python_public"][: max(0, keep_public)]
        print(
            f"warning: capped python_public to {len(buckets['python_public'])} "
            f"to keep jarvis ratio >= {jarvis_ratio_min}",
            file=sys.stderr,
        )

    for domain in (
        "rrf_nxt",
        "python_jarvis",
        "python_public",
        "arduino_wand",
        "cad",
        "mentra",
        "general",
    ):
        for row in buckets.get(domain, []):
            key = (row["prompt"], row["chosen"], row["rejected"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged, counts


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--jarvis-ratio-min", type=float, default=0.4)
    p.add_argument("inputs", type=Path, nargs="+")
    args = p.parse_args(argv)

    merged, counts = merge_files(args.inputs, jarvis_ratio_min=args.jarvis_ratio_min)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for row in merged:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(merged)} merged pairs → {args.out}")
    for name, n in counts.items():
        print(f"  {name}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
