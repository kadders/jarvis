"""Tests for tool segments, bundle import, and resonance analytics."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import numpy as np

from models.analyze_live import analyze_segment, avoid_rpm_bands, spectral_peaks
from models.handler import TapCollectorHandler
from models.import_csv import load_run_bundle, parse_modbus_csv_text
from models.segments import (
    seed_segments_from_tool_events,
    validate_segments,
)
from models.tool_library import ToolLibraryStore, normalize_fusion_tool
from utils.config import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "homing_trim.csv"


def _settings(tmp_path):
    return Settings(
        session_store_dir=tmp_path / "sessions",
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
        session_idle_timeout_s=60.0,
    )


def test_normalize_fusion_reference_rpm():
    rec = normalize_fusion_tool(
        {
            "guid": "g1",
            "description": "EM",
            "geometry": {"DC": 0.25, "NOF": 4},
            "unit": "inches",
            "start-values": {"spindleSpeed": 18000},
            "post-process": {"number": 3},
        }
    )
    assert rec["flutes"] == 4
    assert rec["reference_rpm"] == 18000
    assert rec["reference_rpm_source"] == "fusion_reference"
    assert rec["diameter_mm"] is not None
    assert abs(rec["diameter_mm"] - 0.25 * 25.4) < 0.01


def test_avoid_rpm_bands_and_peaks():
    bands = avoid_rpm_bands(900.0, 4, rpm_min=9000, rpm_max=24000)
    assert bands
    assert bands[0]["harmonic"] == 1
    assert bands[0]["rpm_min"] < bands[0]["center_rpm"] < bands[0]["rpm_max"]

    t = np.arange(0, 1.0, 0.001)
    sig = np.sin(2 * np.pi * 40.0 * t)
    peaks = spectral_peaks(sig, 1000.0, max_peaks=3)
    assert peaks
    assert abs(peaks[0]["hz"] - 40.0) < 2.0


def test_validate_segments_overlap():
    errs = validate_segments(
        [{"t0": 0, "t1": 5}, {"t0": 4, "t1": 8}],
        t_min=0,
        t_max=10,
    )
    assert any("overlap" in e for e in errs)
    assert not validate_segments(
        [{"t0": 0, "t1": 4}, {"t0": 4, "t1": 8}],
        t_min=0,
        t_max=10,
    )


def test_seed_segments_from_tool_events():
    events = [
        {"event": "start", "t_s": 0.0, "tool_number": 1, "tool_name": "A"},
        {"event": "change", "t_s": 10.0, "tool_number": 3, "tool_name": "B"},
        {"event": "stop", "t_s": 20.0, "tool_number": 3},
    ]
    segs = seed_segments_from_tool_events(events, t_end=20.0, t_start=0.0)
    assert len(segs) == 2
    assert segs[0]["tool_number"] == 1
    assert segs[0]["t1"] == 10.0
    assert segs[1]["tool_number"] == 3
    assert segs[1]["t1"] == 20.0


def test_seed_segments_skips_deselect_gap():
    events = [
        {
            "event": "change",
            "event_type": "tool_selected",
            "t_s": 2.0,
            "tool_number": 13,
        },
        {
            "event": "change",
            "event_type": "tool_deselected",
            "t_s": 100.0,
            "tool_number": -1,
        },
        {
            "event": "change",
            "event_type": "tool_selected",
            "t_s": 200.0,
            "tool_number": 11,
        },
        {
            "event": "change",
            "event_type": "tool_selected",
            "t_s": 300.0,
            "tool_number": 26,
        },
    ]
    segs = seed_segments_from_tool_events(events, t_end=400.0, t_start=0.0)
    assert [s["tool_number"] for s in segs] == [13, 11, 26]
    assert segs[0]["t1"] == 100.0
    assert segs[1]["t0"] == 200.0
    assert segs[1]["t1"] == 300.0
    assert segs[2]["t1"] == 400.0


def test_modbus_csv_rpm_provenance():
    text = "t_s,hr_513,ir_0\n0.0,2000,200\n1.0,1800,180\n"
    samples = parse_modbus_csv_text(text)
    sources = {s["source"] for s in samples}
    assert "commanded_modbus" in sources
    assert "measured_modbus" in sources
    cmd = next(s for s in samples if s["source"] == "commanded_modbus")
    meas = next(s for s in samples if s["source"] == "measured_modbus")
    # 2000 / 10 = 200 Hz → 12000 RPM for 2-pole
    assert abs(cmd["rpm"] - 12000) < 1.0
    # ir_0 is always deci-Hz: 200 → 20 Hz → 1200 RPM (not 200 Hz)
    assert abs(meas["rpm"] - 1200) < 1.0
    assert cmd["is_observed"] is False  # commanded is contextual only
    assert meas["is_observed"] is True


def test_load_run_bundle_sidecars(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("run/homing.csv", FIXTURE.read_text())
        events = [
            {
                "event": "start",
                "t_s": 0.0,
                "tool_number": 3,
                "tool_name": "1/4 EM",
                "session_id": "run1",
            },
            {
                "event": "change",
                "t_s": 0.05,
                "tool_number": 5,
                "tool_name": "1/8",
                "session_id": "run1",
            },
        ]
        zf.writestr(
            "run/tool-events.jsonl",
            "\n".join(json.dumps(e) for e in events),
        )
        zf.writestr(
            "run/tools-snapshot.json",
            json.dumps(
                {
                    "tools": [
                        {"number": 3, "name": "1/4 EM", "spindleRpm": 18000},
                        {"number": 5, "name": "1/8", "spindleRpm": 22000},
                    ]
                }
            ),
        )
        zf.writestr(
            "run/run-meta.json",
            json.dumps(
                {
                    "session_id": "20260411_test",
                    "job_file": "part.gcode",
                    "sample_rate_hz": 800.0,
                    "mode": "live_spindle",
                }
            ),
        )
        zf.writestr("run/modbus.csv", "t_s,hr_513,ir_0\n0.01,2000,200\n0.05,1800,180\n")
        zf.writestr(
            "run/spindle-telemetry.jsonl",
            json.dumps(
                {
                    "t_s": 0.02,
                    "source": "arborctl",
                    "hz": 400.0,
                    "rpm": 24000.0,
                    "poles": 2,
                    "commanded_rpm": 24000.0,
                    "power_available": True,
                    "watts": 350.0,
                    "load_percent": 23.3,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "t_s": 0.03,
                    "source": "arborctl",
                    "hz": 80.0,
                    "rpm": 4800.0,
                    "poles": 2,
                    "power_available": False,
                    "h100_fc4_count": 2,
                }
            )
            + "\n",
        )
    bundle = load_run_bundle(buf.getvalue(), filename="run.zip")
    assert "homing.csv" in bundle["source_label"]
    assert len(bundle["tool_events"]) == 2
    assert len(bundle["tools_snapshot"]) == 2
    assert bundle["run_meta"]["job_file"] == "part.gcode"
    assert any(s["source"] == "commanded_modbus" for s in bundle["rpm_samples"])
    assert any(s["source"] == "rrf_spindle" for s in bundle["rpm_samples"])
    assert any(s["source"] == "arborctl" for s in bundle["rpm_samples"])
    arbor = next(s for s in bundle["rpm_samples"] if s["source"] == "arborctl")
    assert arbor["is_observed"] is True
    assert abs(arbor["rpm"] - 24000) < 1.0
    assert len(bundle["load_samples"]) == 1
    assert bundle["load_samples"][0]["load_percent"] == 23.3


def test_import_bundle_seeds_segments(tmp_path):
    settings = _settings(tmp_path)
    lib = ToolLibraryStore(settings.session_store_dir)
    lib.import_bytes(
        json.dumps(
            {
                "data": [
                    {
                        "guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "description": "1/4 EM 4FL",
                        "geometry": {"DC": 0.25, "NOF": 4},
                        "post-process": {"number": 3},
                        "start-values": {"spindleSpeed": 18000},
                    }
                ]
            }
        ).encode(),
        filename="lib.json",
    )
    handler = TapCollectorHandler(settings)
    handler.set_tool_library(
        lookup_by_number=lib.lookup_by_number,
        get_tool=lib.get_tool,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("homing.csv", FIXTURE.read_text())
        zf.writestr(
            "tool-events.jsonl",
            json.dumps(
                {
                    "event": "start",
                    "t_s": 0.0,
                    "tool_number": 3,
                    "tool_name": "1/4 EM",
                }
            )
            + "\n",
        )
        zf.writestr(
            "tools-snapshot.json",
            json.dumps({"tools": [{"number": 3, "name": "1/4 EM", "spindleRpm": 18000}]}),
        )
        zf.writestr(
            "run-meta.json",
            json.dumps({"session_id": "segtest", "job_file": "x.gcode"}),
        )

    async def run():
        return await handler.import_upload(buf.getvalue(), filename="bundle.zip")

    result = asyncio.run(run())
    assert result["n_segments"] >= 1
    seg = result["segments"][0]
    assert seg["tool_number"] == 3
    assert seg["tool_guid"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert seg["flutes"] == 4
    assert result["segment_analysis"]
    sa = result["segment_analysis"][0]
    assert sa["analysis"] is not None
    assert sa["analysis"]["n_teeth"] == 4


def test_ambiguous_tool_number(tmp_path):
    """Segment resolver still handles duplicate numbers if catalog already has them."""
    settings = _settings(tmp_path)
    lib = ToolLibraryStore(settings.session_store_dir)
    # Bypass import number-eviction so we can exercise ambiguous resolution.
    lib._catalog["tools"] = {
        "g-a": {
            "guid": "g-a",
            "description": "A",
            "tool_number": 3,
            "flutes": 4,
            "diameter": 0.25,
        },
        "g-b": {
            "guid": "g-b",
            "description": "B",
            "tool_number": 3,
            "flutes": 2,
            "diameter": 0.125,
        },
    }
    lib._save()
    handler = TapCollectorHandler(settings)
    handler.set_tool_library(
        lookup_by_number=lib.lookup_by_number,
        get_tool=lib.get_tool,
    )

    async def run():
        meta = await handler.import_upload(FIXTURE.read_bytes(), filename="homing.csv")
        sid = meta["session_id"]
        return await handler.set_segments(
            sid,
            [{"t0": meta["t_start"], "t1": meta["t_end"], "tool_number": 3}],
        )

    result = asyncio.run(run())
    seg = result["segments"][0]
    assert seg["tool_guid"] is None
    assert len(seg["ambiguous_tools"]) == 2


def test_set_segments_validation_and_manual_rpm(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        meta = await handler.import_upload(FIXTURE.read_bytes(), filename="homing.csv")
        sid = meta["session_id"]
        t0 = meta["t_start"]
        t1 = meta["t_end"]
        # Overlap should fail
        try:
            await handler.set_segments(
                sid,
                [
                    {"t0": t0, "t1": (t0 + t1) / 2},
                    {"t0": t0, "t1": t1},
                ],
            )
            raised = False
        except ValueError:
            raised = True
        assert raised

        out = await handler.set_segments(
            sid,
            [
                {
                    "t0": t0,
                    "t1": t1,
                    "flutes": 3,
                    "reference_rpm": 15000,
                    "reference_rpm_source": "manual",
                }
            ],
        )
        return out

    result = asyncio.run(run())
    assert result["n_segments"] == 1
    assert result["segments"][0]["flutes"] == 3
    sa = result["segment_analysis"][0]
    assert sa["analysis"]["n_teeth"] == 3
    assert sa["has_observed_rpm"] is True
    assert sa["reference_rpm_risk"]["rpm"] == 15000


def test_analyze_segment_observed_buckets():
    sr = 200.0
    t = np.arange(0, 2.0, 1.0 / sr)
    # Higher vibration in second half
    az = np.where(t < 1.0, 0.05 * np.sin(2 * np.pi * 30 * t), 0.4 * np.sin(2 * np.pi * 30 * t))
    data = np.vstack([np.zeros_like(t), np.zeros_like(t), az])
    rpm_samples = [
        {"t_s": 0.5, "rpm": 12000, "source": "manual", "is_observed": True},
        {"t_s": 1.5, "rpm": 18000, "source": "manual", "is_observed": True},
    ]
    sa = analyze_segment(
        t,
        data,
        sr,
        t0=0.0,
        t1=2.0,
        flute_count=4,
        rpm_samples=rpm_samples,
        reference_rpm=12000,
        reference_rpm_source="manual",
        segment_id="s1",
    )
    assert sa["has_observed_rpm"]
    assert sa["rpm_buckets"]
    # Bad should prefer higher vibration bucket
    if sa["observed_bad_rpm"] and sa["observed_good_rpm"]:
        assert sa["observed_bad_rpm"][0]["score"] >= sa["observed_good_rpm"][0]["score"]


def test_legacy_csv_still_imports(tmp_path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        return await handler.import_upload(FIXTURE.read_bytes(), filename="homing.csv")

    result = asyncio.run(run())
    assert result["origin"] == "import"
    assert result["n_samples"] >= 50
    assert "jarvis" in result["analysis_by_source"]
    # No tool events → no auto segments
    assert result.get("n_segments", 0) == 0
