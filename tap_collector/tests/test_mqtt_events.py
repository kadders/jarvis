"""Tests for MQTT event ring buffer and JSON-key queries."""

from __future__ import annotations

import asyncio
import json
import time

from models.handler import TapCollectorHandler
from models.mqtt_events import (
    MqttEventStore,
    get_by_path,
    match_event,
    parse_where_clause,
    summarize_payload,
)
from utils.config import Settings


def test_get_by_path_and_summarize():
    doc = {"payload": {"event": "change", "nested": {"n": 3}, "ax": list(range(100))}}
    assert get_by_path(doc, "payload.event") == "change"
    assert get_by_path(doc, "payload.nested.n") == 3
    summ = summarize_payload(doc["payload"])
    assert summ["ax"]["n"] == 100
    assert summ["event"] == "change"


def test_parse_where_and_match():
    assert parse_where_clause("event=change") == ("event", "=", "change")
    assert parse_where_clause("fn_hz>=100")[1] == ">="
    assert parse_where_clause("job_file~part")[1] == "~"
    assert parse_where_clause("session_id")[1] == "exists"

    ev = {
        "id": 1,
        "topic": "tap/milo/tool",
        "payload": {"event": "change", "tool_number": 3, "session_id": "s1"},
    }
    assert match_event(ev, where=["event=change", "tool_number=3"])
    assert not match_event(ev, where=["tool_number=9"])
    assert match_event(ev, where=["tool_number>2"])
    assert match_event(ev, topic="milo/tool")
    assert match_event(ev, q="s1")
    assert match_event(ev, where=["session_id"])


def test_store_query_order_and_since(tmp_path):
    store = MqttEventStore(
        max_events=100,
        store_dir=tmp_path / "mqtt_events",
        retention_days=30,
    )
    store.record("tap/d/session", json.dumps({"event": "start", "session_id": "a"}))
    store.record("tap/d/tool", json.dumps({"event": "change", "tool_number": 3}))
    store.record("duet/milo/job", json.dumps({"event": "start", "job_file": "x.gcode"}))

    all_ev = store.query(limit=10)
    assert all_ev["n_total"] == 3
    assert all_ev["events"][0]["id"] == 3  # newest first

    tools = store.query(where=["event=change", "tool_number=3"])
    assert tools["n_returned"] == 1
    assert tools["events"][0]["topic"].endswith("/tool")

    jobs = store.query(where=["job_file~gcode"])
    assert jobs["n_returned"] == 1

    since = store.query(since_id=2, limit=10)
    assert since["n_returned"] == 1
    assert since["events"][0]["id"] == 3


def test_mqtt_events_persist_across_restart(tmp_path):
    store_dir = tmp_path / "sessions" / "mqtt_events"
    a = MqttEventStore(max_events=50, store_dir=store_dir, retention_days=30)
    a.record("tap/d/tool", json.dumps({"event": "change", "tool_number": 7}))
    a.record("tap/d/session", json.dumps({"event": "stop", "session_id": "s"}))
    assert a.summary()["next_id"] == 2
    assert list(store_dir.glob("*.jsonl"))

    b = MqttEventStore(max_events=50, store_dir=store_dir, retention_days=30)
    assert b.summary()["n_events"] == 2
    assert b.summary()["next_id"] == 2
    q = b.query(where=["tool_number=7"])
    assert q["n_returned"] == 1
    # New events continue IDs
    b.record("tap/d/status", json.dumps({"state": "idle"}))
    assert b.summary()["next_id"] == 3


def test_mqtt_events_retention_prune(tmp_path):
    store_dir = tmp_path / "mqtt_events"
    store = MqttEventStore(max_events=50, store_dir=store_dir, retention_days=7)
    old_ts = time.time() - 10 * 86400
    store.record("tap/d/old", json.dumps({"event": "old"}), ts=old_ts)
    store.record("tap/d/new", json.dumps({"event": "new"}))
    day_files = list(store_dir.glob("*.jsonl"))
    assert len(day_files) >= 2
    pruned = store.prune(force=True)
    assert pruned["pruned"] >= 1
    remaining = list(store_dir.glob("*.jsonl"))
    assert len(remaining) == 1
    q = store.query(where=["event=new"])
    assert q["n_returned"] == 1


def test_mqtt_events_record_clamps_small_ts(tmp_path):
    store = MqttEventStore(max_events=50, store_dir=None, retention_days=7)
    ev = store.record("tap/d/test", json.dumps({"event": "x"}), ts=1.0)
    assert ev["ts"] >= 1e9
    assert ev["received_at"] == ev["ts"]
    assert ev["received_at"] >= 1e9


def test_mqtt_events_received_at_on_normal_record(tmp_path):
    store = MqttEventStore(max_events=50, store_dir=None, retention_days=7)
    ev = store.record("tap/d/test", json.dumps({"event": "y"}))
    assert "received_at" in ev
    assert ev["received_at"] == ev["ts"]
    assert ev["ts"] >= 1e9


def test_handler_records_mqtt_stream(tmp_path):
    handler = TapCollectorHandler(
        Settings(
            session_store_dir=tmp_path / "sessions",
            llm_forward_enabled=False,
            publish_jarvis_analysis=False,
            mqtt_event_buffer=200,
            mqtt_event_retention_days=30,
        )
    )

    async def run():
        await handler.handle_message(
            "tap/milo/tool",
            json.dumps(
                {
                    "session_id": "t1",
                    "event": "change",
                    "tool_number": 5,
                    "previous_tool": 1,
                }
            ),
        )
        await handler.handle_message(
            "tap/milo/accel/batch",
            json.dumps(
                {
                    "session_id": "t1",
                    "seq": 0,
                    "ax": [0.1] * 50,
                    "ay": [0.0] * 50,
                    "az": [1.0] * 50,
                }
            ),
        )

    asyncio.run(run())
    q = handler.mqtt_events.query(where=["tool_number=5"])
    assert q["n_returned"] == 1
    batch = handler.mqtt_events.query(topic="accel/batch")
    assert batch["n_returned"] == 1
    payload = batch["events"][0]["payload"]
    assert payload["ax"]["n"] == 50
    assert isinstance(payload["ax"], dict)
    # Persisted under session PVC layout
    assert (tmp_path / "sessions" / "mqtt_events").is_dir()
    assert list((tmp_path / "sessions" / "mqtt_events").glob("*.jsonl"))
