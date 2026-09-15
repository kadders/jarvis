"""Tests for CAM / 4th-combinator MQTT topic ingest (cam/{device}/…)."""

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


def test_parse_mqtt_topics_includes_cam():
    assert _parse_mqtt_topics(None) == ["tap/#", "duet/#", "cam/#"]
    assert _parse_mqtt_topics("") == ["tap/#", "duet/#", "cam/#"]
    assert _parse_mqtt_topics("tap/#, duet/#, cam/#") == ["tap/#", "duet/#", "cam/#"]


def test_cam_sim_session_and_tools(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))
    sid = "20260731_sim"

    async def run():
        await handler.handle_message(
            "cam/milo/status",
            json.dumps(
                {
                    "state": "connected",
                    "device_id": "milo",
                    "mode": "sim",
                    "session_id": sid,
                    "job_file": "part - 4th axis sim.gcode",
                }
            ),
        )
        await handler.handle_message(
            "cam/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": sid,
                    "device_id": "milo",
                    "mode": "sim",
                    "sample_rate_hz": 0.0,
                    "job_file": "part - 4th axis sim.gcode",
                    "tool_number": 3,
                    "tool_name": "1/4 EM",
                    "gcode_summary": {
                        "source": "fourth_combinator_sim",
                        "tools_used": [3, 5],
                        "n_tool_changes": 2,
                    },
                }
            ),
        )
        await handler.handle_message(
            "cam/milo/tool",
            json.dumps(
                {
                    "schema_version": 2,
                    "event_id": "tevt-1",
                    "event": "start",
                    "event_type": "tool_selected",
                    "session_id": sid,
                    "seq": 0,
                    "t_s": 1.0,
                    "tool_number": 3,
                    "previous_tool": None,
                    "tool_name": "1/4 EM",
                    "job_file": "part - 4th axis sim.gcode",
                    "rrf_slot": 3,
                }
            ),
        )
        await handler.handle_message(
            "cam/milo/tool",
            json.dumps(
                {
                    "schema_version": 2,
                    "event_id": "tevt-2",
                    "event": "change",
                    "event_type": "tool_selected",
                    "session_id": sid,
                    "seq": 1,
                    "t_s": 2.0,
                    "tool_number": 5,
                    "previous_tool": 3,
                    "tool_name": "1/8 EM",
                    "job_file": "part - 4th axis sim.gcode",
                    "rrf_slot": 5,
                }
            ),
        )
        await handler.handle_message(
            "cam/milo/session",
            json.dumps(
                {
                    "event": "stop",
                    "event_type": "end_session",
                    "session_id": sid,
                    "device_id": "milo",
                    "mode": "sim",
                    "job_file": "part - 4th axis sim.gcode",
                    "tool_number": 5,
                }
            ),
        )
        await handler.handle_message(
            "cam/milo/status",
            json.dumps(
                {
                    "state": "idle",
                    "device_id": "milo",
                    "mode": "sim",
                    "session_id": sid,
                    "job_file": "part - 4th axis sim.gcode",
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session(sid)
    assert sess is not None
    assert sess["origin"] == "cam"
    assert sess["mode"] == "sim"
    assert sess["job_file"] == "part - 4th axis sim.gcode"
    assert sess["job_file_source"] == "cam"
    assert sess["stopped"] is True
    assert sess["n_tool_events"] == 2
    tools = [e["tool_number"] for e in sess["tool_events"]]
    assert tools == [3, 5]
    assert sess["gcode_summary"]["source"] == "fourth_combinator_sim"
    assert sess["publisher_state"] == "idle"
    # Status connected arrived before session existed; idle after stop is kept
    assert any(e.get("state") == "idle" for e in sess["status_events"])


def test_cam_job_file_does_not_clobber_tap(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "shared",
                    "mode": "live_spindle",
                    "job_file": "0:/gcodes/from-tap.gcode",
                }
            ),
        )
        await handler.handle_message(
            "cam/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "shared",
                    "mode": "sim",
                    "job_file": "from-cam.gcode",
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session("shared")
    assert sess["job_file"] == "0:/gcodes/from-tap.gcode"
    assert sess["job_file_source"] == "tap"


def test_cam_job_file_wins_over_duet(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "duet/milo/job",
            json.dumps({"event": "start", "job_file": "from-duet.gcode"}),
        )
        sessions = handler.list_sessions()
        sid = sessions[0]["session_id"]
        await handler.handle_message(
            "cam/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": sid,
                    "mode": "sim",
                    "job_file": "from-cam.gcode",
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session(handler.list_sessions()[0]["session_id"])
    assert sess["job_file"] == "from-cam.gcode"
    assert sess["job_file_source"] == "cam"
