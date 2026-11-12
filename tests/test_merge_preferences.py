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


def test_merge_includes_cad(tmp_path: Path):
    a = tmp_path / "cad" / "preferences.jsonl"
    b = tmp_path / "rrf_nxt" / "preferences.jsonl"
    _write_prefs(
        a, [{"prompt": "fusion", "chosen": "get_scene_info", "rejected": "blind retry"}]
    )
    _write_prefs(b, [{"prompt": "macro", "chosen": "echo {", "rejected": "echo ("}])
    merged, _ = merge_files([a, b], jarvis_ratio_min=0.4)
    assert len(merged) == 2
    assert any(r["prompt"] == "fusion" for r in merged)


def test_merge_includes_mentra(tmp_path: Path):
    a = tmp_path / "mentra" / "preferences.jsonl"
    b = tmp_path / "cad" / "preferences.jsonl"
    _write_prefs(
        a,
        [
            {
                "prompt": "miniapp",
                "chosen": "use @mentra/miniapp",
                "rejected": "legacy @mentra/sdk",
            }
        ],
    )
    _write_prefs(
        b, [{"prompt": "fusion", "chosen": "get_scene_info", "rejected": "blind retry"}]
    )
    merged, _ = merge_files([a, b], jarvis_ratio_min=0.4)
    assert len(merged) == 2
    assert any(r["prompt"] == "miniapp" for r in merged)
