"""Live Modbus → RPM correlation, transitions, and provenance."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np

from models.analyze_live import analyze_segment
from models.handler import SessionState, TapCollectorHandler
from models.import_csv import (
    modbus_row_to_rpm_samples,
    parse_modbus_csv_text,
    save_timeseries_npz,
    spindle_payload_to_samples,
)
from models.rpm_provenance import is_observed_source, fusion_ref_series
from models.segments import enrich_segments_from_rrf_snapshot
from models.transitions import build_transition_windows, filter_observed_outside_transitions
from utils.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        session_store_dir=tmp_path / "sessions",
        mqtt_host="127.0.0.1",
        modbus_rows_max=100,
        rpm_reanalyze_min_interval_s=0.0,
        transition_half_width_s=0.5,
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
    )


def test_flat_csv_and_mqtt_rows_normalize_identically():
    flat = {"t_s": 1.25, "hr_513": 2000, "ir_0": 200}
    mqtt = {
        "session_id": "s1",
        "t_s": 1.25,
        "ts": 1710000000.0,
        "registers": {"hr_513": 2000, "ir_0": 200},
    }
    a = modbus_row_to_rpm_samples(flat)
    b = modbus_row_to_rpm_samples(mqtt)
    assert len(a) == len(b) == 2
    by_src_a = {s["source"]: s for s in a}
    by_src_b = {s["source"]: s for s in b}
    assert by_src_a["commanded_modbus"]["rpm"] == by_src_b["commanded_modbus"]["rpm"]
    assert by_src_a["measured_modbus"]["rpm"] == by_src_b["measured_modbus"]["rpm"]
    assert abs(by_src_a["commanded_modbus"]["rpm"] - 12000) < 1.0
    # ir_0=200 is always deci-Hz → 20 Hz → 1200 RPM (not 200 Hz)
    assert abs(by_src_a["measured_modbus"]["rpm"] - 1200) < 1.0
    csv_samples = parse_modbus_csv_text("t_s,hr_513,ir_0\n1.25,2000,200\n")
    assert {s["source"] for s in csv_samples} == {
        "commanded_modbus",
        "measured_modbus",
    }


def test_commanded_not_observed_measured_is():
    assert not is_observed_source("commanded_modbus")
    assert not is_observed_source("commanded_rrf")
    assert not is_observed_source("rrf_spindle")
    assert is_observed_source("measured_modbus")
    assert is_observed_source("arborctl")
    assert is_observed_source("manual")


def test_fusion_ref_series_steps_per_segment():
    segments = [
        {"t0": 0.0, "t1": 10.0, "reference_rpm": 12000},
        {"t0": 10.0, "t1": 20.0, "reference_rpm": 18000},
        {"t0": 20.0, "t1": 30.0, "reference_rpm": 9000},
    ]
    ts = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0]
    ys = fusion_ref_series(ts, segments)
    assert ys == [12000.0, 12000.0, 12000.0, 18000.0, 18000.0, 9000.0, 9000.0, None]


def test_enrich_segments_accepts_snake_case_spindle_rpm():
    segs = enrich_segments_from_rrf_snapshot(
        [{"t0": 0, "t1": 1, "tool_number": 3, "reference_rpm": None}],
        [{"number": 3, "name": "EM", "spindle_rpm": 16500}],
    )
    assert segs[0]["reference_rpm"] == 16500
    assert segs[0]["reference_rpm_source"] == "rrf_spindle"
    # camelCase still works
    segs2 = enrich_segments_from_rrf_snapshot(
        [{"t0": 0, "t1": 1, "tool_number": 5, "reference_rpm": None}],
        [{"number": 5, "spindleRpm": 22000}],
    )
    assert segs2[0]["reference_rpm"] == 22000


def test_h100_ir0_always_deci_hz():
    """H100 ir_0 / hr_513 are deci-Hz. Do not treat raw < 1000 as already-Hz."""
    at_80 = {s["source"]: s for s in modbus_row_to_rpm_samples({"t_s": 0.1, "ir_0": 800})}
    assert abs(at_80["measured_modbus"]["rpm"] - 4800) < 1.0  # 80 Hz × 60 (2-pole)
    at_400 = {
        s["source"]: s for s in modbus_row_to_rpm_samples({"t_s": 0.1, "ir_0": 4000})
    }
    assert abs(at_400["measured_modbus"]["rpm"] - 24000) < 1.0  # 400 Hz, 2-pole
    four_pole = {
        s["source"]: s
        for s in modbus_row_to_rpm_samples({"t_s": 0.1, "ir_0": 4000}, poles=4)
    }
    assert abs(four_pole["measured_modbus"]["rpm"] - 12000) < 1.0


def test_arborctl_mqtt_ingest_observed_and_load(tmp_path: Path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/dev/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "s-sp",
                    "mode": "live_spindle",
                    "sample_rate_hz": 800,
                }
            ),
        )
        await handler.handle_message(
            "tap/dev/spindle",
            json.dumps(
                {
                    "session_id": "s-sp",
                    "t_s": 0.25,
                    "source": "arborctl",
                    "hz": 400.0,
                    "rpm": 24000.0,
                    "poles": 2,
                    "commanded_rpm": 24000.0,
                    "commanded_hz": 400.0,
                    "power_available": True,
                    "watts": 350.0,
                    "load_percent": 23.3,
                    "h100_fc4_count": 13,
                }
            ),
        )
        live = handler.live_view("s-sp")
        del handler.sessions["s-sp"]
        hydrated = handler.get_session("s-sp")
        return live, hydrated

    live, hydrated = asyncio.run(run())
    assert live is not None
    sources = {s["source"] for s in live["rpm_samples"]}
    assert "arborctl" in sources
    assert "commanded_rrf" in sources
    arbor = next(s for s in live["rpm_samples"] if s["source"] == "arborctl")
    assert arbor["is_observed"] is True
    assert abs(arbor["rpm"] - 24000) < 1.0
    assert live["n_load_samples"] == 1
    assert live["load_samples"][0]["load_percent"] == 23.3
    assert live["latest_load_percent"] == 23.3
    assert hydrated is not None
    assert hydrated["n_load_samples"] == 1
    assert any(s["source"] == "arborctl" for s in hydrated["rpm_samples"])


def test_arborctl_mqtt_omits_load_when_unavailable(tmp_path: Path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/dev/spindle",
            json.dumps(
                {
                    "session_id": "s-noload",
                    "t_s": 0.5,
                    "source": "arborctl",
                    "hz": 80.0,
                    "rpm": 4800.0,
                    "poles": 2,
                    "power_available": False,
                    "h100_fc4_count": 2,
                }
            ),
        )
        return handler.get_session("s-noload")

    sess = asyncio.run(run())
    assert sess is not None
    assert any(s["source"] == "arborctl" and s["is_observed"] for s in sess["rpm_samples"])
    assert sess["n_load_samples"] == 0
    assert sess.get("load_samples") == []


def test_spindle_payload_parser_splits_speed_and_load():
    rpm, load = spindle_payload_to_samples(
        {
            "t_s": 1.0,
            "source": "arborctl",
            "rpm": 12000.0,
            "hz": 400.0,
            "poles": 4,
            "commanded_rpm": 12000.0,
            "power_available": True,
            "load_percent": 0.0,
            "watts": 0.0,
        }
    )
    by_src = {s["source"]: s for s in rpm}
    assert by_src["arborctl"]["is_observed"] is True
    assert by_src["commanded_rrf"]["is_observed"] is False
    assert load == [{"t_s": 1.0, "load_percent": 0.0, "watts": 0.0}]


def test_epoch_modbus_rejected_with_warning(tmp_path: Path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/dev/modbus",
            json.dumps(
                {
                    "session_id": "s-epoch",
                    "t_s": 1710000000.0,
                    "registers": {"hr_513": 2000, "ir_0": 200},
                }
            ),
        )
        return handler.get_session("s-epoch")

    sess = asyncio.run(run())
    assert sess is not None
    assert sess["n_modbus_rows"] == 0
    assert sess["n_rpm_samples"] == 0
    assert "modbus_t_s_looks_like_wall_epoch" in (sess.get("warnings") or [])
    assert sess.get("rpm_alignment") == "misaligned"


def test_live_modbus_dedupe_persist_hydrate(tmp_path: Path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        for t in (0.5, 0.5, 1.0):
            await handler.handle_message(
                "tap/dev/modbus",
                json.dumps(
                    {
                        "session_id": "s-live",
                        "t_s": t,
                        "ts": 1710000000.0 + t,
                        "registers": {"hr_513": 2000, "ir_0": 200},
                    }
                ),
            )
        live = handler.live_view("s-live")
        del handler.sessions["s-live"]
        hydrated = handler.get_session("s-live")
        return live, hydrated

    live, hydrated = asyncio.run(run())
    assert live is not None
    assert live["n_modbus_rows"] == 3  # raw rows keep duplicates; RPM samples dedupe
    assert live["n_rpm_samples"] == 4  # 2 unique times × measured+commanded
    assert live["latest_measured_rpm"] is not None
    assert live["latest_commanded_rpm"] is not None
    assert live["rpm_samples"]
    assert hydrated is not None
    assert hydrated["n_rpm_samples"] == 4
    assert hydrated["n_modbus_rows"] == 3


def test_transition_windows_exclude_from_rankings_keep_samples():
    windows = build_transition_windows(
        [{"event": "change", "t_s": 1.0, "tool_number": 3}],
        [
            {"t_s": 0.0, "registers": {"co_73": 0, "hr_513": 0}},
            {"t_s": 2.0, "registers": {"co_73": 1, "hr_513": 2000}},
        ],
        half_width_s=0.5,
    )
    kinds = {w["kind"] for w in windows}
    assert "tool_change" in kinds
    assert "spindle_start" in kinds

    samples = [
        {"t_s": 1.0, "rpm": 12000, "source": "measured_modbus"},
        {"t_s": 3.0, "rpm": 15000, "source": "measured_modbus"},
    ]
    kept = filter_observed_outside_transitions(samples, windows)
    assert len(kept) == 1
    assert kept[0]["t_s"] == 3.0

    sr = 200.0
    t = np.arange(0, 4.0, 1.0 / sr)
    az = 0.1 * np.sin(2 * np.pi * 40 * t)
    data = np.vstack([np.zeros_like(t), np.zeros_like(t), az])
    sa = analyze_segment(
        t,
        data,
        sr,
        t0=0.0,
        t1=4.0,
        flute_count=4,
        rpm_samples=samples
        + [
            {"t_s": 1.0, "rpm": 12000, "source": "commanded_modbus"},
        ],
        transition_windows=windows,
        segment_id="seg1",
    )
    assert sa["n_observed_rpm_in_transitions"] >= 1
    assert sa["has_observed_rpm"] is True
    assert any(abs(b["rpm_center"] - 15000) < 1 for b in (sa.get("rpm_buckets") or []))


def test_session_payload_exposes_transition_windows(tmp_path: Path):
    handler = TapCollectorHandler(_settings(tmp_path))

    async def run():
        await handler.handle_message(
            "tap/dev/session",
            json.dumps(
                {
                    "event": "start",
                    "event_type": "start_session",
                    "session_id": "s-tr",
                    "mode": "live_spindle",
                    "sample_rate_hz": 200,
                }
            ),
        )
        await handler.handle_message(
            "tap/dev/tool",
            json.dumps(
                {
                    "session_id": "s-tr",
                    "event": "change",
                    "event_type": "tool_selected",
                    "t_s": 1.0,
                    "tool_number": 2,
                    "tool_name": "EM",
                }
            ),
        )
        await handler.handle_message(
            "tap/dev/modbus",
            json.dumps(
                {
                    "session_id": "s-tr",
                    "t_s": 2.0,
                    "registers": {"hr_513": 2000, "ir_0": 200, "co_73": 1},
                }
            ),
        )
        return handler.get_session("s-tr")

    sess = asyncio.run(run())
    assert sess is not None
    assert sess.get("transition_windows")
    assert any(w["kind"] == "tool_change" for w in sess["transition_windows"])
    assert sess["n_rpm_samples"] >= 2


def test_finalize_creates_measured_rpm_buckets(tmp_path: Path):
    handler = TapCollectorHandler(_settings(tmp_path))
    sr = 200.0
    t = np.arange(0, 2.0, 1.0 / sr)
    az = np.where(
        t < 1.0,
        0.05 * np.sin(2 * np.pi * 30 * t),
        0.4 * np.sin(2 * np.pi * 30 * t),
    )
    data = np.vstack([np.zeros_like(t), np.zeros_like(t), az])
    sid = "s-final"
    npz = tmp_path / "sessions" / f"{sid}.npz"
    npz.parent.mkdir(parents=True, exist_ok=True)
    save_timeseries_npz(npz, t, data, sr)
    sess = SessionState(
        session_id=sid,
        device_id="dev",
        mode="live_spindle",
        sample_rate_hz=sr,
        stopped=True,
        timeseries_path=npz,
        n_samples=int(t.size),
        rpm_samples=[
            {
                "t_s": 0.5,
                "rpm": 12000,
                "source": "measured_modbus",
                "is_observed": True,
            },
            {
                "t_s": 1.5,
                "rpm": 18000,
                "source": "measured_modbus",
                "is_observed": True,
            },
            {
                "t_s": 0.5,
                "rpm": 12000,
                "source": "commanded_modbus",
                "is_observed": False,
            },
        ],
        segments=[
            {
                "segment_id": "seg-a",
                "t0": 0.0,
                "t1": 2.0,
                "tool_number": 1,
                "flutes": 4,
                "state": "closed",
            }
        ],
        rpm_alignment="ok",
    )
    handler.sessions[sid] = sess
    handler._recompute_segment_analysis(sess)
    out = handler.get_session(sid)
    assert out is not None
    assert out["segment_analysis"]
    sa = out["segment_analysis"][0]
    assert sa["has_observed_rpm"] is True
    assert sa["rpm_buckets"]


def test_restore_rpm_from_mqtt_events_when_late(tmp_path: Path):
    """Collector restart mid-job left only late rpm_samples; mqtt_events has full span."""
    settings = _settings(tmp_path)
    settings.telemetry_log_dir = tmp_path / "telemetry"
    settings.telemetry_log_dir.mkdir(parents=True)
    mqtt_dir = settings.session_store_dir / "mqtt_events"
    mqtt_dir.mkdir(parents=True)
    sid = "rpm-restore"
    day = mqtt_dir / "2026-09-07.jsonl"
    rows = []
    for t_s, rpm in [(3.5, 12000), (100.0, 15000), (1800.0, 24000)]:
        rows.append(
            json.dumps(
                {
                    "topic": "tap/milo/spindle",
                    "payload": {
                        "session_id": sid,
                        "t_s": t_s,
                        "rpm": rpm,
                        "source": "arborctl",
                        "commanded_rpm": rpm,
                    },
                }
            )
        )
    day.write_text("\n".join(rows) + "\n", encoding="utf-8")

    handler = TapCollectorHandler(settings)
    sess = SessionState(
        session_id=sid,
        device_id="milo",
        mode="live_spindle",
        stopped=True,
        t_start=3.0,
        rpm_samples=[
            {
                "t_s": 1800.0,
                "rpm": 24000,
                "source": "arborctl",
                "is_observed": True,
                "raw_value": None,
            }
        ],
        warnings=[],
    )
    added = handler._maybe_restore_rpm_from_durable(sess, t_start=3.0)
    assert added >= 4  # early + late measured/commanded
    assert "rebuilt_rpm_from_durable" in sess.warnings
    ts = [float(s["t_s"]) for s in sess.rpm_samples]
    assert min(ts) < 10.0
    assert max(ts) >= 1800.0
    sources = {s["source"] for s in sess.rpm_samples}
    assert "arborctl" in sources
    assert "commanded_rrf" in sources


def test_live_meta_sends_full_rpm_when_stopped(tmp_path: Path):
    settings = _settings(tmp_path)
    handler = TapCollectorHandler(settings)
    samples = [
        {"t_s": float(i), "rpm": 1000.0 + i, "source": "arborctl", "is_observed": True}
        for i in range(600)
    ]
    sess = SessionState(
        session_id="live-rpm",
        device_id="milo",
        stopped=True,
        recording_state="stopped",
        rpm_samples=samples,
    )
    meta = handler._live_meta(sess)
    assert len(meta["rpm_samples"]) == 600
    sess.stopped = False
    sess.recording_state = "recording"
    meta_live = handler._live_meta(sess)
    assert len(meta_live["rpm_samples"]) == settings.rpm_samples_ws_max
