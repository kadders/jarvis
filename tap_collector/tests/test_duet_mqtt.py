"""Tests for Duet MQTT topic ingest (duet/{machine}/…)."""

from __future__ import annotations

import asyncio
import json

from models.handler import TapCollectorHandler
from utils.config import Settings, _parse_mqtt_topics


def _settings(tmp_path):
    return Settings(
        session_store_dir=tmp_path / "sessions",
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
        session_idle_timeout_s=60.0,
        mqtt_topics=["tap/#", "duet/#", "cam/#"],
    )


def test_parse_mqtt_topics():
    assert _parse_mqtt_topics(None) == ["tap/#", "duet/#", "cam/#"]
    assert _parse_mqtt_topics("") == ["tap/#", "duet/#", "cam/#"]
    assert _parse_mqtt_topics("tap/#") == ["tap/#"]
    assert _parse_mqtt_topics("tap/#, duet/#") == ["tap/#", "duet/#"]
    assert _parse_mqtt_topics("tap/#, duet/#, cam/#") == ["tap/#", "duet/#", "cam/#"]


def test_duet_tool_creates_daily_session(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "duet/milo/tool",
            json.dumps(
                {
                    "event": "change",
                    "tool_number": 3,
                    "previous_tool": 1,
                    "t_s": 12.5,
                    "job_file": "part.gcode",
                    "file_position": 900,
                }
            ),
        )

    asyncio.run(run())
    # Daily session id
    sessions = handler.list_sessions()
    assert len(sessions) == 1
    sid = sessions[0]["session_id"]
    assert sid.startswith("duet-milo-")
    assert sessions[0]["origin"] == "duet"
    sess = handler.get_session(sid)
    assert sess["job_file"] == "part.gcode"
    assert sess["n_tool_events"] == 1
    assert sess["tool_events"][0]["tool_number"] == 3
    assert sess["tool_events"][0]["previous_tool"] == 1


def test_duet_job_and_bind_to_active_session(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "duet/milo/job",
            json.dumps(
                {
                    "event": "start",
                    "job_file": "cut.gcode",
                    "status": "processing",
                    "file_position": 0,
                }
            ),
        )
        await handler.handle_message(
            "duet/milo/tool",
            json.dumps({"event": "change", "tool_number": 5, "previous_tool": -1}),
        )
        await handler.handle_message(
            "duet/milo/state",
            json.dumps(
                {
                    "status": "processing",
                    "current_tool": 7,
                    "job_file": "cut.gcode",
                    "file_position": 100,
                }
            ),
        )

    asyncio.run(run())
    sessions = handler.list_sessions()
    assert len(sessions) == 1
    sid = sessions[0]["session_id"]
    sess = handler.get_session(sid)
    assert sess["job_file"] == "cut.gcode"
    assert sess["stopped"] is False
    # start tool from first tool msg + change from state current_tool 5→7
    assert sess["n_tool_events"] >= 2
    tools = [e["tool_number"] for e in sess["tool_events"]]
    assert 5 in tools
    assert 7 in tools
    assert any(e.get("leaf") == "job" for e in sess["status_events"])


def test_duet_message_plaintext(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message("duet/milo/message", "hello from M118")

    asyncio.run(run())
    sess = handler.get_session(handler.list_sessions()[0]["session_id"])
    assert sess["n_status_events"] >= 1
    assert sess["status_events"][-1]["text"] == "hello from M118"


def test_duet_explicit_session_id(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "duet/milo/tool",
            json.dumps(
                {
                    "session_id": "shared-1",
                    "event": "start",
                    "tool_number": 2,
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session("shared-1")
    assert sess is not None
    assert sess["device_id"] == "milo"
    assert sess["origin"] == "duet"
