"""Tests for CSV import and series/spectrum views."""

from __future__ import annotations

import asyncio
import json
import zipfile
from io import BytesIO
from pathlib import Path

import numpy as np

from models.handler import TapCollectorHandler
from models.import_csv import load_upload_bytes, parse_tap_csv_path
from utils.config import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "homing_trim.csv"


def _settings(tmp_path):
    return Settings(
        session_store_dir=tmp_path / "sessions",
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
        session_idle_timeout_s=60.0,
    )


def test_parse_homing_fixture():
    t, data, sr = parse_tap_csv_path(FIXTURE)
    assert t.size >= 50
    assert data.shape == (3, t.size)
    assert abs(sr - 800.0) < 1.0


def test_import_csv_and_series(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))
    raw = FIXTURE.read_bytes()

    async def run():
        return await handler.import_upload(raw, filename="homing.csv", mode="live_spindle")

    result = asyncio.run(run())
    assert result["origin"] == "import"
    assert result["mode"] == "live_spindle"
    assert result["n_samples"] >= 50
    assert "jarvis" in result["analysis_by_source"]
    sid = result["session_id"]
    assert (tmp_path / "sessions" / f"{sid}.npz").is_file()
    assert (tmp_path / "sessions" / f"{sid}.json").is_file()

    series = handler.series_view(sid, max_points=64)
    assert series is not None
    assert series["series"]["n_total"] >= 50
    assert len(series["series"]["t_s"]) <= 64
    assert len(series["series"]["t_s"]) == len(series["series"]["az_g"])

    spectrum = handler.spectrum_view(sid, max_bins=32)
    assert spectrum is not None
    assert spectrum["spectrum"]["n_samples"] > 0
    assert len(spectrum["spectrum"]["freqs_hz"]) <= 32

    listed = handler.list_sessions()
    assert any(s["session_id"] == sid for s in listed)


def test_import_zip_prefers_homing(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("other.csv", "t_s,ax_g,ay_g,az_g\n0,0,0,0\n")
        zf.writestr("run/homing.csv", FIXTURE.read_text())
    raw = buf.getvalue()
    t, data, sr, label = load_upload_bytes(raw, filename="run.zip")
    assert "homing.csv" in label
    assert data.shape[1] >= 50

    async def run():
        return await handler.import_upload(raw, filename="run.zip", mode="live_spindle")

    result = asyncio.run(run())
    assert result["import_label"] and "homing.csv" in result["import_label"]


def test_tool_events_captured_and_persisted(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/milo/session",
            '{"event":"start","session_id":"t1","mode":"live_spindle",'
            '"sample_rate_hz":800.0,"job_file":"part.gcode","tool_number":1}',
        )
        await handler.handle_message(
            "tap/milo/tool",
            '{"session_id":"t1","event":"change","seq":1,"t_s":12.5,'
            '"tool_number":3,"previous_tool":1,"tool_name":"1/4 EM",'
            '"job_file":"part.gcode","file_position":900}',
        )

    asyncio.run(run())
    sess = handler.get_session("t1")
    assert sess is not None
    assert sess["job_file"] == "part.gcode"
    # Session start opens a live segment / active tool but does not invent a
    # synthetic tool-events.jsonl row — only /tool MQTT payloads are stored.
    assert sess["active_tool_number"] == 3
    assert sess["n_tool_events"] == 1
    events = sess["tool_events"]
    assert events[0]["event"] == "change"
    assert events[0]["previous_tool"] == 1
    assert events[0]["tool_number"] == 3
    assert events[0]["t_s"] == 12.5
    assert len(sess["segments"]) >= 1
    # Persisted to disk
    disk = json.loads((tmp_path / "sessions" / "t1.json").read_text())
    assert disk["n_tool_events"] == 1
    assert disk["job_file"] == "part.gcode"
    # Listed summary carries tool count
    listed = {s["session_id"]: s for s in handler.list_sessions()}
    assert listed["t1"]["n_tool_events"] == 1


def test_list_includes_disk_only(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))
    store = tmp_path / "sessions"
    store.mkdir(parents=True, exist_ok=True)
    (store / "disk-only.json").write_text(
        '{"session_id":"disk-only","device_id":"x","mode":"live_spindle",'
        '"stopped":true,"n_batches":0,"n_samples":10,"origin":"import",'
        '"updated_at":1,"analysis_by_source":{}}',
        encoding="utf-8",
    )
    ids = [s["session_id"] for s in handler.list_sessions()]
    assert "disk-only" in ids
