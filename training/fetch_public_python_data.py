#!/usr/bin/env python3
"""
Download and filter public Hugging Face Python DPO datasets.

Writes training/datasets/python_public/{preferences,instructions}.jsonl + sources.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from training.dataset_common import Instruction, Preference, dedupe_prefs, write_jsonl

DEFAULT_SOURCES = (
    "NextWealth/Python-DPO",
    "quangduc1112001/python-code-DPO-fine-tune",
    "PotatoHD/code-edit-dpo",
)

SOURCE_LICENSES = {
    "NextWealth/Python-DPO": "check-dataset-card",
    "quangduc1112001/python-code-DPO-fine-tune": "check-dataset-card",
    "PotatoHD/code-edit-dpo": "mixed-per-row",
}


def _load_hf_dataset(name: str, cache_dir: Optional[Path]):
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("install datasets: pip install datasets") from exc
    kwargs: Dict[str, Any] = {}
    if cache_dir:
        kwargs["cache_dir"] = str(cache_dir)
    return load_dataset(name, split="train", **kwargs)


def _map_nextwealth(row: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    prompt = str(row.get("instruction") or "").strip()
    chosen = str(row.get("chosen_code") or "").strip()
    rejected = str(row.get("rejected_code-1") or row.get("rejected_code_1") or "").strip()
    if not prompt or not chosen or not rejected:
        return None
    return prompt, chosen, rejected


def _map_quangduc(row: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    prompt = str(row.get("prompt") or "").strip()
    chosen = str(row.get("chosen") or "").strip()
    rejected = str(row.get("rejected") or "").strip()
    if not prompt or not chosen or not rejected:
        return None
    return prompt, chosen, rejected


def _map_potato(row: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    lang = str(row.get("language") or row.get("lang") or "").lower()
    rule = str(row.get("rule") or row.get("reason") or "").lower()
    if lang and lang != "python":
        return None
    if lang == "" and "python" not in rule and "format" not in rule and "style" not in rule:
        return None
    prompt = str(row.get("prompt") or "").strip()
    chosen = str(row.get("chosen") or "").strip()
    rejected = str(row.get("rejected") or "").strip()
    if not prompt or not chosen or not rejected:
        return None
    return prompt, chosen, rejected


MAPPERS = {
    "NextWealth/Python-DPO": _map_nextwealth,
    "quangduc1112001/python-code-DPO-fine-tune": _map_quangduc,
    "PotatoHD/code-edit-dpo": _map_potato,
}


def iter_source_rows(
    name: str,
    cache_dir: Optional[Path],
    max_rows: int,
) -> Iterator[Tuple[str, str, str]]:
    ds = _load_hf_dataset(name, cache_dir)
    mapper = MAPPERS.get(name)
    if mapper is None:
        return
    n = 0
    for row in ds:
        mapped = mapper(dict(row))
        if not mapped:
            continue
        prompt, chosen, rejected = mapped
        if chosen == rejected:
            continue
        yield prompt, chosen, rejected
        n += 1
        if n >= max_rows:
            break


def build_from_fixtures(fixtures: List[Dict[str, str]], hf_name: str) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    license_ = SOURCE_LICENSES.get(hf_name, "unknown")
    for row in fixtures:
        prompt = row["prompt"].strip()
        chosen = row["chosen"].strip()
        rejected = row["rejected"].strip()
        if not prompt or chosen == rejected:
            continue
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="python_public",
                source_path=hf_name,
                license=license_,
                tags=["hf", hf_name.split("/")[-1]],
            )
        )
    return prefs, instr


def build_dataset(
    sources: List[str],
    *,
    cache_dir: Optional[Path],
    max_rows_per_source: int,
    max_total: int,
    fixtures_path: Optional[Path] = None,
) -> Tuple[List[Preference], List[Instruction], List[Dict[str, Any]]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    meta_sources: List[Dict[str, Any]] = []

    if fixtures_path and fixtures_path.is_file():
        fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
        for name in sources:
            rows = fixtures.get(name, [])
            p, i = build_from_fixtures(rows, name)
            prefs.extend(p)
            instr.extend(i)
            meta_sources.append({"dataset": name, "rows": len(p), "mode": "fixtures"})
        prefs = dedupe_prefs(prefs)
        return prefs[:max_total], instr[:max_total], meta_sources

    for name in sources:
        before = len(prefs)
        try:
            for prompt, chosen, rejected in iter_source_rows(name, cache_dir, max_rows_per_source):
                if len(prefs) >= max_total:
                    break
                prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
                instr.append(
                    Instruction(
                        prompt=prompt,
                        response=chosen,
                        kind="python_public",
                        source_path=name,
                        license=SOURCE_LICENSES.get(name, "unknown"),
                        tags=["hf", name.split("/")[-1]],
                    )
                )
        except Exception as exc:
            print(f"warning: skipped {name}: {exc}", file=sys.stderr)
        meta_sources.append({"dataset": name, "rows": len(prefs) - before, "mode": "hf"})
        if len(prefs) >= max_total:
            break

    prefs = dedupe_prefs(prefs)
    instr = instr[: len(prefs)]
    return prefs, instr, meta_sources


def main(argv: Optional[List[str]] = None) -> int:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=here / "datasets" / "python_public")
    p.add_argument("--sources", type=str, default=",".join(DEFAULT_SOURCES))
    p.add_argument("--max-rows-per-source", type=int, default=500)
    p.add_argument("--max-total", type=int, default=1500)
    p.add_argument("--cache-dir", type=Path, default=None)
    p.add_argument(
        "--fixtures",
        type=Path,
        default=here / "datasets" / "python_public" / "fixtures.json",
        help="Offline fixture JSON for CI (skip HF download when present and --use-fixtures)",
    )
    p.add_argument("--use-fixtures", action="store_true")
    p.add_argument("--min-pairs", type=int, default=1)
    args = p.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    fixtures = args.fixtures if args.use_fixtures else None
    prefs, instr, meta = build_dataset(
        sources,
        cache_dir=args.cache_dir,
        max_rows_per_source=args.max_rows_per_source,
        max_total=args.max_total,
        fixtures_path=fixtures,
    )
    if len(prefs) < args.min_pairs:
        print(f"refusing to write: got {len(prefs)} pairs", file=sys.stderr)
        return 3

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    n_p = write_jsonl(out_dir / "preferences.jsonl", (x.to_dict() for x in prefs))
    n_i = write_jsonl(out_dir / "instructions.jsonl", (x.to_dict() for x in instr))
    (out_dir / "sources.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    readme = f"""# Public Python DPO dataset (filtered)

| Preference rows | {n_p} |
| Sources | {', '.join(sources)} |

Regenerate (HF): `python -m training.fetch_public_python_data --out-dir {out_dir}`

CI/offline: `python -m training.fetch_public_python_data --use-fixtures`
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    print(f"wrote {n_p} preferences → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
