"""Unit tests for preference export (no GPU)."""
import json
from pathlib import Path

from training.export_preferences import main, to_pair


def test_to_pair_from_correction():
    pair = to_pair(
        {
            "prompt": "capital?",
            "response": "Berlin",
            "rating": -1,
            "correction": "Paris",
        }
    )
    assert pair == {
        "prompt": "capital?",
        "chosen": "Paris",
        "rejected": "Berlin",
    }


def test_export_cli(tmp_path: Path):
    fb = tmp_path / "feedback.jsonl"
    out = tmp_path / "prefs.jsonl"
    fb.write_text(
        json.dumps(
            {
                "prompt": "2+2",
                "response": "5",
                "rating": -1,
                "correction": "4",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert main(["--feedback", str(fb), "--out", str(out), "--min-pairs", "1"]) == 0
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["chosen"] == "4"
    assert rec["rejected"] == "5"
