"""Tests for merge_preferences."""
from __future__ import annotations

import json
from pathlib import Path

from training.merge_preferences import merge_files


def _write_prefs(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_merge_dedupes(tmp_path: Path):
    a = tmp_path / "rrf_nxt" / "preferences.jsonl"
    b = tmp_path / "python_jarvis" / "preferences.jsonl"
    row = {"prompt": "p", "chosen": "c", "rejected": "r"}
    _write_prefs(a, [row, row])
    _write_prefs(b, [{"prompt": "p2", "chosen": "c2", "rejected": "r2"}])
    merged, counts = merge_files([a, b], jarvis_ratio_min=0.4)
    assert len(merged) == 2
    assert sum(counts.values()) == 3
