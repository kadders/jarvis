"""Spindle-aware spectrum / avoid-RPM regressions."""

from __future__ import annotations

import numpy as np

from models.analyze_live import analyze_session_arrays, dominant_frequency_hz
from utils.config import Settings, get_settings


def _tone(
    freq_hz: float,
    sr: float,
    duration_s: float = 2.0,
    axis: str = "z",
    *,
    phase: str = "sin",
) -> tuple[np.ndarray, np.ndarray]:
    t = np.arange(0, duration_s, 1.0 / sr)
    # Exact Nyquist sine is identically zero on even grids; use cosine there.
    if phase == "cos" or abs(freq_hz - sr / 2.0) < 1e-9:
        sig = np.cos(2 * np.pi * freq_hz * t)
    else:
        sig = np.sin(2 * np.pi * freq_hz * t)
    zeros = np.zeros_like(t)
    if axis == "x":
        data = np.vstack([sig, zeros, zeros])
    elif axis == "y":
        data = np.vstack([zeros, sig, zeros])
    else:
        data = np.vstack([zeros, zeros, sig])
    return t, data


def test_400hz_tone_not_high_confidence_fn():
    """At 800 S/s, 400 Hz is spindle/Nyquist — must not become machining fₙ."""
    t, data = _tone(400.0, 800.0, duration_s=4.0, axis="z", phase="cos")
    result = analyze_session_arrays(
        t,
        data,
        800.0,
        flute_count=4,
        spindle_freq_hz=400.0,
        mode="continuous",
    )
    assert result["spindle_freq_hz"] == 400.0
    assert result["nyquist_hz"] == 400.0
    assert result["reference_rpm"] == 24000.0
    assert result["suggestion_confidence"] == "none"
    assert result["suggested_rpm_min"] is None
    assert result["suggested_rpm_max"] is None
    assert result["fn_hz"] is None or result.get("fn_rejected_as_spindle")
    assert "fn_near_spindle_operating_frequency" in (result.get("warnings") or []) or result[
        "fn_hz"
    ] is None
    peaks = result.get("peaks") or []
    assert any(p.get("label") == "spindle_1x_suspect" for p in peaks)


def test_180hz_modal_on_one_axis_not_doubled():
    """Highest-variance axis recovers ~180 Hz (not 360 via magnitude)."""
    t, data = _tone(180.0, 800.0, duration_s=2.0, axis="y")
    result = analyze_session_arrays(
        t,
        data,
        800.0,
        flute_count=4,
        spindle_freq_hz=400.0,
        mode="impact",
        min_rpm=9000.0,
        max_rpm=24000.0,
    )
    assert result["axis_used"] == "y"
    assert result["fn_hz"] is not None
    assert 170.0 < float(result["fn_hz"]) < 190.0
    # Avoid RPMs for 4 flutes: 60*180/(4*k) → 2700, 1350, ... — none in 9k–24k
    # Use a higher fn that lands in-band: 180 Hz with 1 flute would, but we keep 4.
    # For in-range avoids need fn such that 60*fn/(Nt*k) in [9k,24k].
    # With Nt=4: fn in [600, 1600] for k=1. So 180 won't produce in-range — that's ok.
    # Verify no fake full-range pocket when no in-range avoids:
    if result["n_avoid_in_range"] == 0:
        assert result["suggested_rpm_min"] is None
        assert result["suggestion_confidence"] == "none"


def test_modal_fn_produces_in_range_avoids():
    """~800 Hz is above Nyquist at 800 S/s — use 200 Hz with 1 flute for in-range line."""
    # RPM_k = 60*fn/(Nt*k). Want in [9000,24000].
    # fn=200, Nt=1 → 12000 for k=1.
    t, data = _tone(200.0, 800.0, duration_s=1.5, axis="z")
    result = analyze_session_arrays(
        t,
        data,
        800.0,
        flute_count=1,
        spindle_freq_hz=400.0,
        mode="impact",
    )
    assert result["fn_hz"] is not None
    assert 190.0 < float(result["fn_hz"]) < 210.0
    assert result["n_avoid_in_range"] >= 1
    assert result["suggested_rpm_min"] is not None
    assert result["suggestion_confidence"] in ("high", "medium", "low")
    # Must not span the entire spindle range as a fake "good" pocket
    span = float(result["suggested_rpm_max"]) - float(result["suggested_rpm_min"])
    assert span < (24000 - 9000) * 0.95


def test_fn_near_400_no_fake_full_range_pocket():
    t, data = _tone(400.0, 800.0, duration_s=1.0, axis="x")
    result = analyze_session_arrays(t, data, 800.0, flute_count=4, spindle_freq_hz=400.0)
    assert result["suggested_rpm_min"] is None
    assert result["suggested_rpm_max"] is None
    assert result["suggestion_confidence"] == "none"


def test_config_default_spindle_freq(monkeypatch, tmp_path):
    get_settings.cache_clear()
    monkeypatch.setenv("TAP_COLLECTOR_SPINDLE_FREQ_HZ", "400")
    monkeypatch.delenv("TAP_COLLECTOR_MAX_RPM", raising=False)
    monkeypatch.setenv("TAP_COLLECTOR_SESSION_DIR", str(tmp_path / "sessions"))
    get_settings.cache_clear()
    s = get_settings()
    assert s.default_spindle_freq_hz == 400.0
    assert s.default_max_rpm == 24000.0
    get_settings.cache_clear()


def test_dominant_frequency_helper():
    sr = 800.0
    t = np.arange(0, 1.0, 1.0 / sr)
    sig = np.sin(2 * np.pi * 100.0 * t)
    assert 95.0 < dominant_frequency_hz(sig, sr) < 105.0


def test_settings_model_default():
    s = Settings(session_store_dir="/tmp/x")
    assert s.default_spindle_freq_hz == 400.0


def test_input_shaping_in_session_analysis():
    t, data = _tone(180.0, 800.0, duration_s=2.0, axis="y")
    result = analyze_session_arrays(
        t,
        data,
        800.0,
        flute_count=4,
        spindle_freq_hz=400.0,
        mode="impact",
    )
    shaping = result.get("input_shaping")
    assert shaping is not None
    assert "M593" in shaping["m593_gcode"]
    assert 170.0 < shaping["freq_hz"] < 190.0
    assert shaping["shaper_type"] == "zvd"


def test_input_shaping_none_when_fn_rejected():
    t, data = _tone(400.0, 800.0, duration_s=4.0, axis="z", phase="cos")
    result = analyze_session_arrays(
        t,
        data,
        800.0,
        flute_count=4,
        spindle_freq_hz=400.0,
        mode="continuous",
    )
    assert result.get("input_shaping") is None
