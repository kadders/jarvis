"""Tests for tap_collector session handler and local analysis."""

from __future__ import annotations

import asyncio
import json

import numpy as np

from models.analyze_live import analyze_session_arrays, dominant_frequency_hz, live_spectrum
from models.handler import TapCollectorHandler
from utils.config import Settings


def _settings(tmp_path):
    return Settings(
        session_store_dir=tmp_path / "sessions",
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
        session_idle_timeout_s=60.0,
    )


def test_session_batch_and_finalize(tmp_path):
    settings = _settings(tmp_path)
    handler = TapCollectorHandler(settings)
    published: list[tuple[str, dict, int]] = []

    def pub(topic, payload, qos=0):
        published.append((topic, payload, qos))

    handler.set_publisher(pub)
    handler.settings.publish_jarvis_analysis = True

    async def run():
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps(
                {
                    "event": "start",
                    "session_id": "s1",
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                    "ts": 1.0,
                }
            ),
        )
        t = np.arange(0, 1.0, 0.01)
        az = np.sin(2 * np.pi * 12.0 * t) + 1.0
        ax = np.zeros_like(t)
        ay = np.zeros_like(t)
        await handler.handle_message(
            "tap/dev1/accel/batch",
            json.dumps(
                {
                    "session_id": "s1",
                    "seq": 0,
                    "t0_s": 0.0,
                    "dt_s": 0.01,
                    "ax": ax.tolist(),
                    "ay": ay.tolist(),
                    "az": az.tolist(),
                }
            ),
        )
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps(
                {
                    "event": "stop",
                    "session_id": "s1",
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                    "ts": 2.0,
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session("s1")
    assert sess is not None
    assert "jarvis" in sess["analysis_by_source"]
    fn = sess["analysis_by_source"]["jarvis"]["fn_hz"]
    assert 10.0 < fn < 14.0
    assert published
    live = handler.live_view("s1")
    assert live is not None
    assert live["spectrum"]["n_samples"] > 0


def test_pi_analysis_not_overwritten(tmp_path):
    settings = _settings(tmp_path)
    handler = TapCollectorHandler(settings)

    async def run():
        await handler.handle_message(
            "tap/dev1/analysis",
            json.dumps(
                {
                    "session_id": "s2",
                    "source": "pi",
                    "fn_hz": 900.0,
                    "avoid_rpm": [13500],
                    "suggested_rpm_min": 15000,
                    "suggested_rpm_max": 18000,
                    "n_teeth": 4,
                    "sample_rate_hz": 800.0,
                }
            ),
        )
        await handler.handle_message(
            "tap/dev1/analysis",
            json.dumps(
                {
                    "session_id": "s2",
                    "source": "pi",
                    "fn_hz": 1.0,
                }
            ),
        )

    asyncio.run(run())
    sess = handler.get_session("s2")
    assert sess["analysis_by_source"]["pi"]["fn_hz"] == 900.0


def test_dominant_frequency():
    sr = 200.0
    t = np.arange(0, 2.0, 1.0 / sr)
    sig = np.sin(2 * np.pi * 25.0 * t)
    fn = dominant_frequency_hz(sig, sr)
    assert 23.0 < fn < 27.0


def test_live_spectrum_empty():
    out = live_spectrum([], [], [], 800.0)
    assert out["fn_hz"] is None


def test_live_spectrum_accepts_numpy():
    sr = 200.0
    t = np.arange(0, 1.0, 1.0 / sr)
    az = np.sin(2 * np.pi * 25.0 * t)
    ax = np.zeros_like(t)
    ay = np.zeros_like(t)
    out = live_spectrum(ax, ay, az, sr)
    assert out["n_samples"] == len(t)
    assert out["fn_hz"] is not None
    assert len(out["freqs_hz"]) == len(out["mag"])


def test_uplot_x_scale_disables_unix_time():
    """SPA must set scales.x.time=false so recording t_s is not shown as Dec 1969."""
    from pathlib import Path

    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    text = app_js.read_text(encoding="utf-8")
    assert "function mergeUplotScales" in text
    assert "function withNumericXAxis" in text
    assert "time: false" in text
    assert "fmtAxisNum" in text
    assert "function fusionRefSeries" in text
    assert "Fusion/tooling ref (per segment)" in text


def test_ui_preview_written_on_finalize_and_served_from_cache(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    handler = TapCollectorHandler(settings)

    async def run():
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps(
                {
                    "event": "start",
                    "session_id": "s-ui",
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                    "ts": 1.0,
                }
            ),
        )
        t = np.arange(0, 1.0, 0.01)
        az = np.sin(2 * np.pi * 12.0 * t) + 1.0
        ax = np.zeros_like(t)
        ay = np.zeros_like(t)
        await handler.handle_message(
            "tap/dev1/accel/batch",
            json.dumps(
                {
                    "session_id": "s-ui",
                    "seq": 0,
                    "t0_s": 0.0,
                    "dt_s": 0.01,
                    "ax": ax.tolist(),
                    "ay": ay.tolist(),
                    "az": az.tolist(),
                }
            ),
        )
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps(
                {
                    "event": "stop",
                    "session_id": "s-ui",
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                    "ts": 2.0,
                }
            ),
        )

    asyncio.run(run())

    ui_path = settings.session_store_dir / "s-ui.ui.json"
    assert ui_path.is_file()
    preview = json.loads(ui_path.read_text(encoding="utf-8"))
    assert preview["schema_version"] == 1
    assert preview["series"]["n_returned"] > 0
    assert isinstance(preview["spectrum"]["freqs_hz"], list)

    # Subsequent views must not reload NPZ when sidecar is present.
    calls = {"n": 0}
    real_load = handler._load_arrays

    def counting_load(session_id):
        calls["n"] += 1
        return real_load(session_id)

    monkeypatch.setattr(handler, "_load_arrays", counting_load)

    series = handler.series_view("s-ui", max_points=500)
    spectrum = handler.spectrum_view("s-ui", max_bins=64)
    assert series is not None and series.get("from_cache") is True
    assert spectrum is not None and spectrum.get("from_cache") is True
    assert calls["n"] == 0
    assert series["series"]["n_returned"] > 0
    assert len(spectrum["spectrum"]["freqs_hz"]) <= 64

    # regenerate forces NPZ path and rewrites cache
    series2 = handler.series_view("s-ui", max_points=500, regenerate=True)
    assert series2 is not None and series2.get("from_cache") is False
    assert calls["n"] >= 1

    # Missing sidecar falls back to NPZ
    ui_path.unlink()
    calls["n"] = 0
    series3 = handler.series_view("s-ui", max_points=500)
    assert series3 is not None and series3.get("from_cache") is False
    assert calls["n"] >= 1
    assert ui_path.is_file()  # rebuilt on miss


def test_ui_preview_removed_on_delete(tmp_path):
    settings = _settings(tmp_path)
    handler = TapCollectorHandler(settings)

    async def run():
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps(
                {
                    "event": "start",
                    "session_id": "s-del",
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                    "ts": 1.0,
                }
            ),
        )
        t = np.arange(0, 0.5, 0.01)
        await handler.handle_message(
            "tap/dev1/accel/batch",
            json.dumps(
                {
                    "session_id": "s-del",
                    "seq": 0,
                    "t0_s": 0.0,
                    "dt_s": 0.01,
                    "ax": np.zeros_like(t).tolist(),
                    "ay": np.zeros_like(t).tolist(),
                    "az": np.sin(2 * np.pi * 10.0 * t).tolist(),
                }
            ),
        )
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps(
                {
                    "event": "stop",
                    "session_id": "s-del",
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                    "ts": 2.0,
                }
            ),
        )

    asyncio.run(run())
    ui_path = settings.session_store_dir / "s-del.ui.json"
    assert ui_path.is_file()
    result = asyncio.run(handler.delete_session("s-del"))
    assert result["deleted"] is True
    assert not ui_path.exists()
    assert "s-del.ui.json" in result["removed"]


def test_list_sessions_skips_ui_preview_sidecars(tmp_path):
    settings = _settings(tmp_path)
    handler = TapCollectorHandler(settings)
    store = settings.session_store_dir
    store.mkdir(parents=True, exist_ok=True)
    (store / "real.json").write_text(
        json.dumps(
            {
                "session_id": "real",
                "device_id": "dev1",
                "stopped": True,
                "n_samples": 10,
            }
        ),
        encoding="utf-8",
    )
    (store / "real.ui.json").write_text(
        json.dumps({"schema_version": 1, "session_id": "real", "series": {}, "spectrum": {}}),
        encoding="utf-8",
    )
    sessions = handler.list_sessions()
    ids = [s["session_id"] for s in sessions]
    assert ids == ["real"]


def test_analyze_session_local():
    sr = 200.0
    t = np.arange(0, 1.0, 1.0 / sr)
    az = np.sin(2 * np.pi * 40.0 * t)
    data = np.vstack([np.zeros_like(t), np.zeros_like(t), az])
    result = analyze_session_arrays(t, data, sr, flute_count=4)
    assert result["source"] == "jarvis"
    assert 35.0 < result["fn_hz"] < 45.0
    assert result["n_teeth"] == 4


def test_llm_forward_posts_summary(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.llm_forward_enabled = True
    settings.jarvis_llm_base_url = "http://llm.test/api/v1"
    handler = TapCollectorHandler(settings)
    calls: list[dict] = []

    class FakeResp:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append({"url": url, "json": json, "headers": headers})
            return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    async def run():
        await handler.handle_message(
            "tap/dev1/analysis",
            json.dumps(
                {
                    "session_id": "s-llm",
                    "source": "pi",
                    "fn_hz": 900.0,
                    "avoid_rpm": [13500],
                    "suggested_rpm_min": 15000,
                    "suggested_rpm_max": 18000,
                    "n_teeth": 4,
                    "sample_rate_hz": 800.0,
                }
            ),
        )

    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/events")
    assert calls[0]["json"]["event_type"] == "data_analysis"
    assert calls[0]["json"]["source"] == "tap-testing"
    analysis = calls[0]["json"]["content"]["data"]["analysis"]
    assert analysis["fn_hz"] == 900.0
    assert "ax" not in analysis
    assert "ay" not in analysis
    assert "az" not in analysis
