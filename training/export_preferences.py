#!/usr/bin/env python3
"""Export feedback JSONL → DPO preference pairs {prompt, chosen, rejected}."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List


def iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def to_pair(rec: Dict[str, Any]) -> Dict[str, str] | None:
    prompt = (rec.get("prompt") or "").strip()
    if not prompt:
        return None
    chosen = (
        (rec.get("preferred") or rec.get("correction") or rec.get("chosen") or "")
        .strip()
    )
    rejected = (rec.get("rejected") or "").strip()
    if not rejected and rec.get("rating") is not None and int(rec["rating"]) < 0:
        rejected = (rec.get("response") or "").strip()
    if not chosen or not rejected:
        return None
    if chosen == rejected:
        return None
    return {"prompt": prompt, "chosen": chosen, "rejected": rejected}


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--feedback",
        type=Path,
        default=Path("cache/llm_feedback/feedback.jsonl"),
        help="Input feedback JSONL",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("cache/llm_adapters/preferences.jsonl"),
        help="Output preference JSONL",
    )
    p.add_argument(
        "--min-pairs",
        type=int,
        default=1,
        help="Refuse to write if fewer than this many pairs (0 to allow empty)",
    )
    args = p.parse_args(argv)

    if not args.feedback.is_file():
        print(f"feedback file not found: {args.feedback}", file=sys.stderr)
        return 2

    pairs: List[Dict[str, str]] = []
    seen = set()
    for rec in iter_jsonl(args.feedback):
        pair = to_pair(rec)
        if not pair:
            continue
        key = (pair["prompt"], pair["chosen"], pair["rejected"])
        if key in seen:
            continue
        seen.add(key)
        pairs.append(pair)

    if len(pairs) < args.min_pairs:
        print(
            f"refusing to write: got {len(pairs)} pairs, need >= {args.min_pairs}",
            file=sys.stderr,
        )
        return 3

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")
    print(f"wrote {len(pairs)} preference pairs → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
