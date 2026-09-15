"""Finalize/recovery restores tool timeline + motion from durable logs."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from models.handler import TapCollectorHandler
from models.replay import merge_tool_event_lists, tool_events_from_mqtt_day_files
from utils.config import Settings


def _settings(tmp_path, **kwargs):
    base = dict(
        session_store_dir=tmp_path / "sessions",
        telemetry_log_dir=tmp_path / "telemetry",
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
        session_idle_timeout_s=60.0,
        motion_samples_max=10000,
    )
    base.update(kwargs)
    return Settings(**base)


def _write_accel(path: Path, sid: str, t0: float = 0.0, n_batches: int = 5):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_batches):
            b = {
                "session_id": sid,
                "seq": i,
                "t0_s": t0 + i * 0.04,
                "dt_s": 0.01,
                "ax": [0.1] * 4,
                "ay": [0.2] * 4,
                "az": [-1.0] * 4,
            }
            row = {
                "session_id": sid,
                "device_id": "milo",
                "_ingest_ts": 1_700_000_000.0 + i,
                "_leaf": "accel/batch",
                "payload": b,
                **b,
            }
            fh.write(json.dumps(row) + "\n")


def _write_motion(path: Path, sid: str, samples: list[tuple[float, int]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for t_s, tool in samples:
            payload = {
                "session_id": sid,
                "t_s": t_s,
                "tool_number": tool,
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
            }
            row = {
                "session_id": sid,
                "device_id": "milo",
                "_leaf": "motion",
                "_ingest_ts": 1_700_000_000.0 + t_s,
                "payload": payload,
                **payload,
            }
            fh.write(json.dumps(row) + "\n")


def _write_tool_jsonl(path: Path, sid: str, events: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            payload = {"session_id": sid, **ev}
            row = {
                "session_id": sid,
                "device_id": "milo",
                "_leaf": "tool",
                "_ingest_ts": time.time(),
                "payload": payload,
                **payload,
            }
            fh.write(json.dumps(row) + "\n")


def _tool_ev(t_s: float, tn: int, eid: str, et: str = "tool_selected") -> dict:
    return {
        "event": "change" if et != "tool_selected" or tn >= 0 else "start",
        "event_type": et,
        "t_s": t_s,
        "tool_number": tn,
        "event_id": eid,
        "seq": int(t_s),
    }


def test_bridge_style_tool_jsonl_finalize_rebuilds_segments(tmp_path):
    sid = "20260907_fake"
    settings = _settings(tmp_path)
    telem = settings.telemetry_log_dir / sid
    _write_accel(telem / "accel.jsonl", sid, t0=0.0, n_batches=20)
    events = [
        _tool_ev(2.0, 13, "e1"),
        _tool_ev(100.0, -1, "e2", et="tool_deselected"),
        _tool_ev(200.0, 11, "e3"),
        _tool_ev(300.0, 26, "e4"),
    ]
    _write_tool_jsonl(telem / "tool.jsonl", sid, events)
    _write_motion(
        telem / "motion.jsonl",
        sid,
        [(3.0, 13), (50.0, 13), (210.0, 11), (310.0, 26)],
    )

    handler = TapCollectorHandler(settings)

    async def run():
        # Late-only live tools + late motion (post-restart), incomplete segment.
        await handler.handle_message(
            "tap/milo/tool",
            json.dumps(
                {
                    "session_id": sid,
                    **_tool_ev(300.0, 26, "e4"),
                }
            ),
        )
        sess = handler.sessions[sid]
        assert len(sess.segments) == 1
        assert sess.segments[0]["tool_number"] == 26
        await handler.handle_message(
            "tap/milo/motion",
            json.dumps(
                {
                    "session_id": sid,
                    "t_s": 310.0,
                    "tool_number": 26,
                    "x": 0,
                    "y": 0,
                    "z": 0,
                }
            ),
        )
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "end_session",
                    "session_id": sid,
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                    "ts": time.time(),
                }
            ),
        )

    asyncio.run(run())
    meta = handler.get_session(sid)
    assert meta is not None
    assert meta["n_tool_events"] >= 4
    tools = {e["tool_number"] for e in meta["tool_events"]}
    assert {13, 11, 26, -1} <= tools
    assert len(meta["segments"]) == 3
    assert [s["tool_number"] for s in meta["segments"]] == [13, 11, 26]
    assert meta["n_motion_samples"] >= 4
    assert float(meta["motion_samples"][0]["t_s"]) < 10.0
    assert "rebuilt_tool_events_from_durable" in meta["warnings"]
    assert "rebuilt_motion_from_jsonl" in meta["warnings"]


def test_mqtt_fallback_merges_tools(tmp_path):
    sid = "mqtt_tools"
    settings = _settings(tmp_path)
    mqtt_dir = settings.session_store_dir / "mqtt_events"
    mqtt_dir.mkdir(parents=True)
    day = mqtt_dir / "2026-09-07.jsonl"
    events = [
        _tool_ev(2.0, 13, "m1"),
        _tool_ev(50.0, 11, "m2"),
        _tool_ev(90.0, 26, "m3"),
    ]
    with day.open("w", encoding="utf-8") as fh:
        for i, ev in enumerate(events):
            row = {
                "id": i + 1,
                "ts": time.time(),
                "topic": "tap/milo/tool",
                "payload": {"session_id": sid, **ev},
            }
            fh.write(json.dumps(row) + "\n")
    loaded = tool_events_from_mqtt_day_files(mqtt_dir, sid)
    assert len(loaded) == 3
    merged = merge_tool_event_lists([{"event_id": "m3", "t_s": 90.0, "tool_number": 26}], loaded)
    assert len(merged) == 3
    assert [e["tool_number"] for e in merged] == [13, 11, 26]


def test_recover_open_hydrates_tool_events(tmp_path):
    sid = "live_tools"
    settings = _settings(tmp_path)
    _write_accel(settings.telemetry_log_dir / sid / "accel.jsonl", sid)
    # Disk JSON already has early tools from pre-restart persists.
    settings.session_store_dir.mkdir(parents=True, exist_ok=True)
    (settings.session_store_dir / f"{sid}.json").write_text(
        json.dumps(
            {
                "session_id": sid,
                "device_id": "milo",
                "stopped": False,
                "recording_state": "recording",
                "tool_events": [_tool_ev(2.0, 13, "early")],
                "motion_samples": [{"t_s": 3.0, "tool_number": 13}],
            }
        ),
        encoding="utf-8",
    )
    path = settings.telemetry_log_dir / sid / "accel.jsonl"
    path.touch()
    handler = TapCollectorHandler(settings)
    assert handler.recover_open_sessions_from_logs() == 1
    sess = handler.sessions[sid]
    assert len(sess.tool_events) == 1
    assert sess.tool_events[0]["tool_number"] == 13
    assert sess.motion_samples and sess.motion_samples[0]["t_s"] == 3.0


def test_get_or_create_hydrates_from_disk(tmp_path):
    sid = "hydrate_me"
    settings = _settings(tmp_path)
    settings.session_store_dir.mkdir(parents=True, exist_ok=True)
    (settings.session_store_dir / f"{sid}.json").write_text(
        json.dumps(
            {
                "session_id": sid,
                "device_id": "milo",
                "stopped": False,
                "tool_events": [_tool_ev(1.0, 13, "disk")],
            }
        ),
        encoding="utf-8",
    )
    handler = TapCollectorHandler(settings)

    async def run():
        sess = await handler._get_or_create(sid, "milo")
        assert len(sess.tool_events) == 1
        assert sess.tool_events[0]["event_id"] == "disk"

    asyncio.run(run())
