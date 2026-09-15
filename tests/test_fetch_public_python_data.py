"""Tests for public Python HF dataset fetcher (fixtures only in CI)."""
from __future__ import annotations

import json
from pathlib import Path

from training.fetch_public_python_data import build_dataset, main


def test_build_from_fixtures(tmp_path: Path):
    fixtures = {
        "NextWealth/Python-DPO": [
            {
                "prompt": "sum list",
                "chosen": "def s(a): return sum(a)",
                "rejected": "def s(a):\n return sum(a)",
            }
        ]
    }
    fx_path = tmp_path / "fixtures.json"
    fx_path.write_text(json.dumps(fixtures), encoding="utf-8")
    prefs, instr, meta = build_dataset(
        ["NextWealth/Python-DPO"],
        cache_dir=None,
        max_rows_per_source=10,
        max_total=10,
        fixtures_path=fx_path,
    )
    assert len(prefs) == 1
    assert meta[0]["mode"] == "fixtures"


def test_fetch_cli_fixtures(tmp_path: Path):
    fixtures = {
        "quangduc1112001/python-code-DPO-fine-tune": [
            {
                "prompt": "p",
                "chosen": "a",
                "rejected": "b",
            }
        ]
    }
    fx = tmp_path / "fixtures.json"
    fx.write_text(json.dumps(fixtures), encoding="utf-8")
    out = tmp_path / "out"
    rc = main(
        [
            "--out-dir",
            str(out),
            "--sources",
            "quangduc1112001/python-code-DPO-fine-tune",
            "--fixtures",
            str(fx),
            "--use-fixtures",
            "--min-pairs",
            "1",
        ]
    )
    assert rc == 0
    assert (out / "preferences.jsonl").is_file()
