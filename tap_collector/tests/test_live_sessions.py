"""Live recording sessions: lifecycle, tool segments, offsets, REST/WS payloads."""

from __future__ import annotations

import asyncio
import json

import numpy as np

from models.handler import TapCollectorHandler
from models.segments import is_selection_tool_event
from utils.config import Settings


def _settings(tmp_path):
    return Settings(
        session_store_dir=tmp_path / "sessions",
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
        session_idle_timeout_s=60.0,
    )


def _batch(sid: str, seq: int, t0: float, n: int = 20, dt: float = 0.01, freq: float = 12.0):
    t = np.arange(n) * dt + t0
    az = np.sin(2 * np.pi * freq * t) + 1.0
    return {
        "session_id": sid,
        "seq": seq,
        "t0_s": t0,
        "dt_s": dt,
        "ax": np.zeros(n).tolist(),
        "ay": np.zeros(n).tolist(),
        "az": az.tolist(),
    }


async def _run_live_multi_tool(handler: TapCollectorHandler, sid: str = "live1"):
    await handler.handle_message(
        "tap/milo/session",
        json.dumps(
            {
                "event": "start",
                "event_type": "start_session",
                "session_id": sid,
                "device_id": "milo",
                "mode": "live_spindle",
                "sample_rate_hz": 100.0,
                "tool_number": 1,
                "tool_name": "EM-1",
                "ts": 1.0,
            }
        ),
    )
    await handler.handle_message(
        "tap/milo/tool",
        json.dumps(
            {
                "event": "start",
                "event_type": "tool_selected",
                "schema_version": 2,
                "event_id": "te-1",
                "session_id": sid,
                "tool_number": 1,
                "previous_tool": -1,
                "tool_name": "EM-1",
                "t_s": 0.0,
                "ts": 1.01,
                "time_basis": "recording_monotonic",
                "offsets": {
                    "kind": "rrf_tool_axis",
                    "unit": "mm",
                    "axes": {"X": 0.0, "Y": 0.0, "Z": -40.0},
                    "offsets_probed": True,
                },
                "tool_snapshot": {
                    "number": 1,
                    "name": "EM-1",
                    "offsets_mm": {"X": 0.0, "Y": 0.0, "Z": -40.0},
                    "offsets_probed": True,
                },
            }
        ),
    )
    await handler.handle_message(
        "tap/milo/accel/batch",
        json.dumps(_batch(sid, 0, 0.0)),
    )
    # Offset-only change must not split the segment
    await handler.handle_message(
        "tap/milo/tool",
        json.dumps(
            {
                "event": "change",
                "event_type": "tool_offset_changed",
                "schema_version": 2,
                "event_id": "te-off-1",
                "session_id": sid,
                "tool_number": 1,
                "t_s": 0.15,
                "ts": 1.2,
                "offsets": {
                    "kind": "rrf_tool_axis",
                    "unit": "mm",
                    "axes": {"X": 0.1, "Y": 0.0, "Z": -40.0},
                    "offsets_probed": True,
                },
                "tool_snapshot": {
                    "number": 1,
                    "name": "EM-1",
                    "offsets_mm": {"X": 0.1, "Y": 0.0, "Z": -40.0},
                },
            }
        ),
    )
    await handler.handle_message(
        "tap/milo/accel/batch",
        json.dumps(_batch(sid, 1, 0.2)),
    )
    # Tool change → close seg1, open seg2
    await handler.handle_message(
        "tap/milo/tool",
        json.dumps(
            {
                "event": "change",
                "event_type": "tool_selected",
                "schema_version": 2,
                "event_id": "te-2",
                "session_id": sid,
                "tool_number": 3,
                "previous_tool": 1,
                "tool_name": "EM-3",
                "t_s": 0.4,
                "ts": 1.5,
                "offsets": {
                    "kind": "rrf_tool_axis",
                    "unit": "mm",
                    "axes": {"X": 0.0, "Y": 0.0, "Z": -55.0},
                },
                "tool_snapshot": {
                    "number": 3,
                    "name": "EM-3",
                    "offsets_mm": {"X": 0.0, "Y": 0.0, "Z": -55.0},
                },
            }
        ),
    )
    await handler.handle_message(
        "tap/milo/accel/batch",
        json.dumps(_batch(sid, 2, 0.4, freq=18.0)),
    )
    await handler.handle_message(
        "tap/milo/session",
        json.dumps(
            {
                "event": "stop",
                "event_type": "end_session",
                "session_id": sid,
                "device_id": "milo",
                "mode": "live_spindle",
                "n_batches": 3,
                "n_samples": 60,
                "tool_number": 3,
                "ts": 2.0,
            }
        ),
    )


def test_is_selection_skips_offset_events():
    assert not is_selection_tool_event(
        {"event": "change", "event_type": "tool_offset_changed"}
    )
    assert is_selection_tool_event(
        {"event": "change", "event_type": "tool_selected"}
    )
    assert is_selection_tool_event({"event": "start"})


def test_live_multi_tool_contiguous_segments(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))
    handler.set_tool_library(
        lookup_by_number=lambda n: (
            [{"guid": f"guid-{n}", "description": f"Tool {n}", "flutes": 4}]
            if n in (1, 3)
            else []
        ),
        get_tool=lambda g: {"guid": g, "flutes": 4, "description": g},
    )
    asyncio.run(_run_live_multi_tool(handler))

    sess = handler.get_session("live1")
    assert sess is not None
    assert sess["recording_state"] == "stopped"
    assert sess["stopped"] is True
    segs = sess["segments"]
    assert len(segs) == 2
    assert segs[0]["tool_number"] == 1
    assert segs[0]["rrf_slot"] == 1
    assert segs[0]["tool_guid"] == "guid-1"
    assert segs[0]["state"] == "closed"
    assert segs[1]["tool_number"] == 3
    assert segs[1]["tool_guid"] == "guid-3"
    # Contiguous: first closes at tool-change t_s, second opens there
    assert abs(float(segs[0]["t1"]) - 0.4) < 1e-9
    assert abs(float(segs[1]["t0"]) - 0.4) < 1e-9
    assert float(segs[1]["t1"]) > float(segs[1]["t0"])
    # Offset update persisted on first segment
    off = segs[0].get("offsets") or {}
    axes = off.get("axes") or {}
    assert abs(float(axes.get("X", 0)) - 0.1) < 1e-9
    # Full tool events preserved
    assert len(sess["tool_events"]) >= 3
    assert any(e.get("event_type") == "tool_offset_changed" for e in sess["tool_events"])
    assert sess["active_tool_number"] == 3
    assert sess["active_tool_guid"] == "guid-3"
    # Whole-session analysis present
    assert "jarvis" in sess["analysis_by_source"]
    assert sess["segment_analysis"]


def test_start_session_end_session_aliases(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/d/session",
            json.dumps(
                {
                    "event_type": "start_session",
                    "session_id": "alias1",
                    "sample_rate_hz": 50.0,
                    "tool_number": 2,
                    "ts": 1.0,
                }
            ),
        )
        await handler.handle_message(
            "tap/d/accel/batch",
            json.dumps(_batch("alias1", 0, 0.0, n=10, dt=0.02)),
        )
        await handler.handle_message(
            "tap/d/session",
            json.dumps(
                {
                    "event_type": "end_session",
                    "session_id": "alias1",
                    "ts": 2.0,
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session("alias1")
    assert sess["stopped"] is True
    assert sess["recording_state"] == "stopped"
    assert len(sess["segments"]) >= 1


def test_duplicate_lifecycle_and_tool_events_idempotent(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        payload = {
            "event": "start",
            "event_type": "start_session",
            "session_id": "dup1",
            "sample_rate_hz": 100.0,
            "tool_number": 1,
            "ts": 10.0,
        }
        await handler.handle_message("tap/d/session", json.dumps(payload))
        await handler.handle_message("tap/d/session", json.dumps(payload))
        tool = {
            "event": "start",
            "event_type": "tool_selected",
            "event_id": "same-id",
            "session_id": "dup1",
            "tool_number": 1,
            "t_s": 0.0,
        }
        await handler.handle_message("tap/d/tool", json.dumps(tool))
        await handler.handle_message("tap/d/tool", json.dumps(tool))

    asyncio.run(run())
    sess = handler.get_session("dup1")
    # One start + one tool event (duplicate tool dropped)
    assert len([e for e in sess["tool_events"] if e.get("event_id") == "same-id"]) == 1
    # Still a single open/closed segment lineage (not doubled by duplicate start)
    assert len(sess["segments"]) == 1


def test_batch_after_stop_flagged(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/d/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "stop1",
                    "sample_rate_hz": 100.0,
                    "ts": 1.0,
                }
            ),
        )
        await handler.handle_message(
            "tap/d/accel/batch",
            json.dumps(_batch("stop1", 0, 0.0)),
        )
        await handler.handle_message(
            "tap/d/session",
            json.dumps(
                {
                    "event": "stop",
                    "event_type": "end_session",
                    "session_id": "stop1",
                    "ts": 2.0,
                }
            ),
        )
        await handler.handle_message(
            "tap/d/accel/batch",
            json.dumps(_batch("stop1", 1, 0.2)),
        )

    asyncio.run(run())
    sess = handler.get_session("stop1")
    assert "batch_after_stop" in (sess.get("warnings") or [])
    assert sess["stopped"] is True


def test_live_view_and_summary_payloads(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))
    handler.set_tool_library(
        lookup_by_number=lambda n: [{"guid": "g1", "description": "EM", "flutes": 3}]
        if n == 1
        else [],
        get_tool=lambda g: {"guid": g, "flutes": 3},
    )

    async def run():
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "lv1",
                    "sample_rate_hz": 100.0,
                    "tool_number": 1,
                    "ts": 1.0,
                }
            ),
        )
        await handler.handle_message(
            "tap/milo/accel/batch",
            json.dumps(_batch("lv1", 0, 0.0)),
        )
        await handler.handle_message(
            "tap/milo/status",
            json.dumps(
                {
                    "state": "recording",
                    "session_id": "lv1",
                    "ts": 1.1,
                }
            ),
        )

    asyncio.run(run())
    live = handler.live_view("lv1")
    assert live is not None
    assert live["recording_state"] == "recording"
    assert live["active_tool_number"] == 1
    assert live["active_tool_guid"] == "g1"
    assert live["active_segment"] is not None
    assert live["active_segment"]["state"] == "open"
    assert live.get("series") is not None
    assert live.get("final") is False
    summaries = handler.list_sessions()
    row = next(s for s in summaries if s["session_id"] == "lv1")
    assert row["recording_state"] == "recording"
    assert row["active_tool_number"] == 1


def test_deferred_segment_until_tool_selected(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "defer1",
                    "sample_rate_hz": 100.0,
                    "ts": 1.0,
                }
            ),
        )
        await handler.handle_message(
            "tap/milo/accel/batch",
            json.dumps(_batch("defer1", 0, 0.0)),
        )
        assert handler.get_session("defer1")["segments"] == []
        await handler.handle_message(
            "tap/milo/tool",
            json.dumps(
                {
                    "event": "change",
                    "event_type": "tool_selected",
                    "event_id": "te-d1",
                    "session_id": "defer1",
                    "tool_number": 4,
                    "t_s": 0.1,
                }
            ),
        )
        await handler.handle_message(
            "tap/milo/accel/batch",
            json.dumps(_batch("defer1", 1, 0.2)),
        )
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "stop",
                    "event_type": "end_session",
                    "session_id": "defer1",
                    "ts": 2.0,
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session("defer1")
    assert len(sess["segments"]) == 1
    assert sess["segments"][0]["tool_number"] == 4
    assert abs(float(sess["segments"][0]["t0"]) - 0.1) < 1e-9
    assert sess["has_timeseries"] is True
    npz = tmp_path / "sessions" / "defer1.npz"
    assert npz.is_file()


def test_feed_chipload_stamped_on_segment(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))
    handler.set_tool_library(
        lookup_by_number=lambda n: [
            {
                "guid": "guid-7",
                "description": "EM7",
                "flutes": 3,
                "feed_mm_min": 1350.0,
                "chipload_mm": 0.025,
                "cutting_preset_name": "Al rough",
                "reference_rpm": 18000.0,
                "reference_rpm_source": "fusion_reference",
            }
        ]
        if n == 7
        else [],
        get_tool=lambda g: {
            "guid": g,
            "flutes": 3,
            "feed_mm_min": 1350.0,
            "chipload_mm": 0.025,
        },
    )

    async def run():
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "feed1",
                    "sample_rate_hz": 100.0,
                    "tool_number": 7,
                    "ts": 1.0,
                }
            ),
        )
        await handler.handle_message(
            "tap/milo/accel/batch",
            json.dumps(_batch("feed1", 0, 0.0)),
        )
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "stop",
                    "event_type": "end_session",
                    "session_id": "feed1",
                    "ts": 2.0,
                }
            ),
        )

    asyncio.run(run())
    seg = handler.get_session("feed1")["segments"][0]
    assert seg["feed_mm_min"] == 1350.0
    assert seg["chipload_mm"] == 0.025
    assert seg["cutting_preset_name"] == "Al rough"


def test_tap_job_file_not_clobbered_by_duet(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "job1",
                    "sample_rate_hz": 100.0,
                    "job_file": "0:/gcodes/from-tap.gcode",
                    "tool_number": 1,
                    "ts": 1.0,
                }
            ),
        )
        await handler.handle_message(
            "duet/milo/job",
            json.dumps(
                {
                    "event": "update",
                    "session_id": "job1",
                    "job_file": "from-duet.gcode",
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session("job1")
    assert sess["job_file"] == "0:/gcodes/from-tap.gcode"
    assert sess["job_file_source"] == "tap"


def test_cam_gcode_resolved_on_start(tmp_path):
    cam = tmp_path / "CAM" / "Proj"
    cam.mkdir(parents=True)
    body = b";OPERATION: Face\nT3\nM3 S18000\nG1 X10 F1350\n"
    (cam / "from-tap.gcode").write_bytes(body)
    settings = _settings(tmp_path)
    settings = settings.model_copy(update={"cam_gcode_dir": tmp_path / "CAM"})
    handler = TapCollectorHandler(settings)

    async def run():
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "cam1",
                    "sample_rate_hz": 100.0,
                    "job_file": "0:/gcodes/from-tap.gcode",
                    "tool_number": 3,
                    "ts": 1.0,
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session("cam1")
    assert sess["gcode_resolution_status"] == "resolved"
    assert sess["source_ref"] == "Proj/from-tap.gcode"
    assert sess["gcode_summary"]["tools_used"] == [3]
    assert sess["gcode_sha256"]


def test_persisted_hydration_keeps_tool_and_segments(tmp_path):
    settings = _settings(tmp_path)
    handler = TapCollectorHandler(settings)
    handler.set_tool_library(
        lookup_by_number=lambda n: [{"guid": f"guid-{n}", "flutes": 2}],
        get_tool=lambda g: {"guid": g, "flutes": 2},
    )
    asyncio.run(_run_live_multi_tool(handler, sid="persist1"))

    # New handler hydrates from disk
    handler2 = TapCollectorHandler(settings)
    meta = handler2.get_session("persist1")
    assert meta is not None
    assert len(meta["segments"]) == 2
    assert any(e.get("event_type") == "tool_offset_changed" for e in meta["tool_events"])
    live = handler2.live_view("persist1")
    assert live is not None
    assert live["recording_state"] == "stopped"
    assert live.get("final") is True


def test_tool_event_coerces_recording_relative_ts(tmp_path):
    """Payload ts: 1.0 is recording-relative in tests; session stores wall-clock ts."""
    settings = _settings(tmp_path)
    handler = TapCollectorHandler(settings)
    sid = "ts-coerce"

    async def run():
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": sid,
                    "device_id": "milo",
                    "mode": "live_spindle",
                    "sample_rate_hz": 100.0,
                    "tool_number": 1,
                    "ts": 1.0,
                }
            ),
        )
        await handler.handle_message(
            "tap/milo/tool",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "tool_selected",
                    "schema_version": 2,
                    "event_id": "te-ts",
                    "session_id": sid,
                    "tool_number": 1,
                    "t_s": 1.5,
                    "ts": 1.0,
                    "time_basis": "recording_monotonic",
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session(sid)
    assert sess is not None
    tool_ev = sess["tool_events"][-1]
    assert tool_ev["t_s"] == 1.5
    assert tool_ev["ts"] >= 1e9
