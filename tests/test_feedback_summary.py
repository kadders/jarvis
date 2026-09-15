"""Feedback summary / list APIs for the Learning UI."""
from __future__ import annotations

from pathlib import Path

from utils.feedback_store import FeedbackStore, is_ask_local_record, slice_tags_for


def _seed(store: FeedbackStore) -> None:
    store.append(
        {
            "prompt": "Query: capital of France?\nResponse:",
            "response": "Berlin",
            "rating": -1,
            "correction": "Paris",
            "rejected": "Berlin",
            "preferred": "Paris",
            "source": "mcp",
            "metadata": {"kind": "outcome"},
            "created_at": 1700000001.0,
        }
    )
    store.append(
        {
            "prompt": "jarvis_ask_local: what is 2+2",
            "response": "4",
            "rating": 1,
            "source": "mcp",
            "metadata": {"kind": "outcome"},
            "created_at": 1700000002.0,
        }
    )
    store.append(
        {
            "prompt": "Implement probe calibration UI",
            "response": "done",
            "rating": 1,
            "source": "mcp",
            "metadata": {"kind": "outcome"},
            "created_at": 1700000003.0,
        }
    )
    store.append(
        {
            "prompt": "chat thumbs",
            "response": "ok",
            "rating": 0,
            "source": "ui",
            "created_at": 1700000004.0,
        }
    )


def test_ask_local_heuristic():
    assert is_ask_local_record({"prompt": "Query: hello\nResponse:"})
    assert is_ask_local_record({"prompt": "jarvis_ask_local: foo"})
    assert not is_ask_local_record({"prompt": "Implement feature X"})


def test_summary_slices(tmp_path: Path):
    store = FeedbackStore(str(tmp_path / "feedback.jsonl"), max_inject=3)
    _seed(store)
    s = store.summary(day_limit=30)
    assert s["count"] == 4
    assert s["by_rating"]["-1"] == 1
    assert s["by_rating"]["1"] == 2
    assert s["by_rating"]["0"] == 1
    assert s["slices"]["ask_local"]["n"] == 2
    assert s["slices"]["ask_local"]["judged"] == 2
    assert s["slices"]["ask_local"]["accuracy"] == 0.5
    assert s["slices"]["agent_outcome"]["n"] == 1
    assert s["slices"]["agent_outcome"]["accuracy"] == 1.0
    assert s["slices"]["mcp"]["n"] == 3
    assert s["with_correction"] == 1
    assert s["exportable_pairs"] == 1


def test_list_filters_and_get(tmp_path: Path):
    store = FeedbackStore(str(tmp_path / "feedback.jsonl"), max_inject=3)
    _seed(store)
    bad = store.list_records(rating=-1, limit=10)
    assert bad["n_total"] == 1
    assert bad["items"][0]["has_correction"] is True
    assert "Berlin" in bad["items"][0]["response_preview"]
    assert "ask_local" in bad["items"][0]["slice_tags"]

    ask = store.list_records(slice_name="ask_local")
    assert ask["n_total"] == 2

    agent = store.list_records(slice_name="agent_outcome")
    assert agent["n_total"] == 1

    fid = bad["items"][0]["feedback_id"]
    full = store.get(fid)
    assert full is not None
    assert full["correction"] == "Paris"
    assert "ask_local" in slice_tags_for(full)


def test_domain_slice_and_tags(tmp_path):
    store = FeedbackStore(str(tmp_path / "fb.jsonl"))
    rec = store.append(
        {
            "rating": -1,
            "prompt": "fix esp32 loop",
            "response": "bad",
            "correction": "use millis",
            "source": "mcp",
            "metadata": {"kind": "outcome", "domain": "arduino_wand"},
        }
    )
    tags = slice_tags_for(rec)
    assert "arduino_wand" in tags
    listed = store.list_records(slice_name="arduino_wand")
    assert listed["n_total"] == 1
    summary = store.summary()
    assert "arduino_wand" in summary["slices"]
