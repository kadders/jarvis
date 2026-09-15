"""Live and session-end analysis for reassembled accel batches."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from models.cutting_force import cutting_force_envelope
from models.rpm_provenance import is_observed_source
from models.transitions import filter_observed_outside_transitions

AXIS_NAMES = ("x", "y", "z")
SPINDLE_ABS_TOL_HZ = 10.0
SPINDLE_REL_TOL = 0.05


def _rpm_to_avoid(fn_hz: float, n_teeth: int, harmonic_orders: int, rpm_max: float) -> list[float]:
    """RPM values where tooth-passing harmonics meet natural frequency."""
    if fn_hz <= 0 or n_teeth <= 0:
        return []
    out: list[float] = []
    for k in range(1, harmonic_orders + 1):
        rpm = (fn_hz * 60.0) / (n_teeth * k)
        if 0 < rpm <= rpm_max:
            out.append(float(rpm))
    return out


def _near_spindle(freq_hz: float, spindle_freq_hz: float) -> bool:
    if freq_hz <= 0 or spindle_freq_hz <= 0:
        return False
    tol = max(SPINDLE_ABS_TOL_HZ, spindle_freq_hz * SPINDLE_REL_TOL)
    return abs(freq_hz - spindle_freq_hz) <= tol


def _suggested_rpm_range(
    avoid: list[float],
    rpm_min: float,
    rpm_max: float,
) -> tuple[float | None, float | None, str, list[str]]:
    """
    Pick a gap between avoid RPMs inside [rpm_min, rpm_max].

    Empty in-range avoid list → no suggested pocket (do not claim the whole range).
    Near-full-span pocket → low confidence.
    """
    warnings: list[str] = []
    band = sorted(r for r in avoid if rpm_min <= r <= rpm_max)
    if not band:
        warnings.append("no_in_range_avoid_rpms")
        return None, None, "none", warnings

    points = [rpm_min] + band + [rpm_max]
    best = (rpm_min, rpm_max)
    best_span = 0.0
    for i in range(len(points) - 1):
        lo, hi = points[i], points[i + 1]
        a, b = lo + (hi - lo) * 0.15, hi - (hi - lo) * 0.15
        if b > a and (b - a) > best_span:
            best_span = b - a
            best = (a, b)

    span_frac = best_span / max(rpm_max - rpm_min, 1.0)
    if span_frac >= 0.85:
        warnings.append("near_full_range_suggested_pocket")
        return float(best[0]), float(best[1]), "low", warnings
    return float(best[0]), float(best[1]), "high", warnings


def avoid_rpm_bands(
    fn_hz: float,
    n_teeth: int,
    *,
    harmonic_orders: int = 5,
    rpm_min: float = 9000.0,
    rpm_max: float = 24000.0,
    fn_uncertainty_hz: float | None = None,
    relative_tol: float = 0.03,
) -> list[dict[str, Any]]:
    """
    Predicted resonance-risk RPM bands (not safety guarantees).

    Band half-width combines absolute fn uncertainty and a relative tolerance.
    """
    if fn_hz <= 0 or n_teeth <= 0:
        return []
    abs_tol = float(fn_uncertainty_hz) if fn_uncertainty_hz else max(2.0, fn_hz * 0.02)
    bands: list[dict[str, Any]] = []
    for k in range(1, harmonic_orders + 1):
        center = (fn_hz * 60.0) / (n_teeth * k)
        if center <= 0 or center > rpm_max * 1.05:
            continue
        rpm_per_hz = 60.0 / (n_teeth * k)
        half = max(abs_tol * rpm_per_hz, center * relative_tol)
        lo = max(0.0, center - half)
        hi = center + half
        if hi < rpm_min or lo > rpm_max:
            continue
        bands.append(
            {
                "harmonic": k,
                "center_rpm": float(center),
                "rpm_min": float(max(lo, rpm_min * 0.5)),
                "rpm_max": float(min(hi, rpm_max * 1.05)),
                "label": f"k={k} tooth-pass ↔ fₙ",
            }
        )
    return bands


def dominant_frequency_hz(signal: np.ndarray, sample_rate_hz: float) -> float:
    if signal.size < 8 or sample_rate_hz <= 0:
        return 0.0
    x = signal - np.mean(signal)
    window = np.hanning(len(x))
    spec = np.fft.rfft(x * window)
    freqs = np.fft.rfftfreq(len(x), d=1.0 / sample_rate_hz)
    mag = np.abs(spec)
    if mag.size <= 1:
        return 0.0
    idx = int(np.argmax(mag[1:]) + 1)
    return float(freqs[idx])


def highest_variance_axis(data: np.ndarray) -> tuple[str, np.ndarray, int]:
    """Return (axis_name, signal, axis_index) for the highest-variance channel."""
    ax, ay, az = data[0], data[1], data[2]
    vars_ = (float(np.var(ax)), float(np.var(ay)), float(np.var(az)))
    idx = int(np.argmax(vars_))
    return AXIS_NAMES[idx], (ax, ay, az)[idx], idx


def spectral_peaks(
    signal: np.ndarray,
    sample_rate_hz: float,
    *,
    max_peaks: int = 8,
    min_hz: float = 5.0,
    spindle_freq_hz: float | None = None,
) -> list[dict[str, Any]]:
    """Top spectral peaks of a 1-D signal (post-mean removal + Hann)."""
    if signal.size < 8 or sample_rate_hz <= 0:
        return []
    x = signal.astype(float) - float(np.mean(signal))
    window = np.hanning(len(x))
    spec = np.fft.rfft(x * window)
    freqs = np.fft.rfftfreq(len(x), d=1.0 / sample_rate_hz)
    mag = np.abs(spec)
    if mag.size <= 1:
        return []
    mask = freqs >= min_hz
    if not np.any(mask):
        return []
    idxs = np.where(mask)[0]
    order = idxs[np.argsort(mag[idxs])[::-1]]
    peaks: list[dict[str, Any]] = []
    used: list[float] = []
    for idx in order:
        f = float(freqs[idx])
        if any(abs(f - u) < 2.0 for u in used):
            continue
        label = "modal_candidate"
        if spindle_freq_hz and _near_spindle(f, spindle_freq_hz):
            label = "spindle_1x_suspect"
        peaks.append({"hz": f, "mag": float(mag[idx]), "label": label})
        used.append(f)
        if len(peaks) >= max_peaks:
            break
    return peaks


def pick_modal_fn(
    peaks: list[dict[str, Any]],
    spindle_freq_hz: float,
    *,
    nyquist_hz: float,
) -> tuple[float | None, list[str]]:
    """
    Choose machining fₙ from labeled peaks, rejecting spindle/Nyquist line.
    """
    warnings: list[str] = []
    modal = [p for p in peaks if p.get("label") != "spindle_1x_suspect"]
    # Also reject peaks within a few Hz of Nyquist when Nyquist ≈ spindle
    filtered: list[dict[str, Any]] = []
    for p in modal:
        f = float(p["hz"])
        if nyquist_hz > 0 and abs(f - nyquist_hz) <= max(SPINDLE_ABS_TOL_HZ, nyquist_hz * SPINDLE_REL_TOL):
            warnings.append("peak_near_nyquist_rejected")
            continue
        # Prefer peaks in the useful modal band (< ~0.875 * Nyquist)
        if nyquist_hz > 0 and f > nyquist_hz * 0.875:
            continue
        filtered.append(p)

    discarded_spindle = any(p.get("label") == "spindle_1x_suspect" for p in peaks)
    if discarded_spindle:
        warnings.append("fn_near_spindle_operating_frequency")

    if filtered:
        return float(filtered[0]["hz"]), warnings

    # No trustworthy modal peak
    if discarded_spindle:
        return None, warnings + ["no_confident_modal_fn"]
    if peaks:
        # Fall back to strongest peak but flag low confidence via caller
        return float(peaks[0]["hz"]), warnings + ["fn_fallback_dominant_peak"]
    return None, warnings + ["no_spectral_peaks"]


def _format_m593_input_shaping(
    natural_freq_hz: float,
    damping_ratio: float | None = None,
    shaper_type: str = "zvd",
) -> str:
    """Local M593 line builder (mirrors tap_testing.analyze.format_m593_input_shaping)."""
    if natural_freq_hz <= 0:
        return 'M593 P"none" ; invalid or missing natural frequency'
    valid_types = frozenset({"none", "zvd", "zvdd", "zvddd", "mzv", "ei2", "ei3", "custom"})
    p_type = shaper_type.lower().strip()
    if p_type not in valid_types:
        p_type = "zvd"
    s = f'M593 P"{p_type}" F{natural_freq_hz:.2f}'
    zeta = damping_ratio if damping_ratio is not None else 0.1
    if 0 < zeta < 1:
        s += f" S{zeta:.3f}"
    else:
        s += " S0.1"
    return s


def input_shaping_recommendation(
    natural_freq_hz: float | None,
    damping_ratio: float | None = None,
    shaper_type: str = "zvd",
) -> dict[str, Any] | None:
    """RRF M593 recommendation dict for API/UI; None when fₙ is unavailable."""
    if natural_freq_hz is None or natural_freq_hz <= 0:
        return None
    try:
        from tap_testing.analyze import input_shaping_recommendation as tt_shaping  # type: ignore

        return tt_shaping(natural_freq_hz, damping_ratio=damping_ratio, shaper_type=shaper_type)
    except Exception:
        zeta = damping_ratio if damping_ratio is not None else 0.1
        if not (0 < zeta < 1):
            zeta = 0.1
        p_type = shaper_type.lower().strip()
        return {
            "m593_gcode": _format_m593_input_shaping(
                float(natural_freq_hz), damping_ratio=damping_ratio, shaper_type=p_type
            ),
            "shaper_type": p_type if p_type in {"none", "zvd", "zvdd", "zvddd", "mzv", "ei2", "ei3", "custom"} else "zvd",
            "freq_hz": float(natural_freq_hz),
            "damping": float(zeta),
        }


def _estimate_damping_ratio(
    t: np.ndarray,
    data: np.ndarray,
    natural_freq_hz: float | None,
    sample_rate_hz: float,
    *,
    analysis_mode: str,
) -> float | None:
    """Optional ζ from decay envelope (impact/tap recordings only)."""
    if analysis_mode != "impact" or natural_freq_hz is None or natural_freq_hz <= 0:
        return None
    if t.size < 16 or data.shape[1] < 16 or sample_rate_hz <= 0:
        return None
    mag = np.sqrt(data[0] ** 2 + data[1] ** 2 + data[2] ** 2)
    try:
        from tap_testing.analyze import decay_envelope_and_damping  # type: ignore

        _, _, zeta = decay_envelope_and_damping(t, mag, float(natural_freq_hz), sample_rate_hz)
        return float(zeta) if zeta is not None else None
    except Exception:
        return None


def _infer_analysis_mode(mode: str | None, duration_s: float) -> str:
    m = (mode or "").strip().lower()
    if m in ("impact", "tap", "cycle"):
        return "impact"
    if m in ("continuous", "live_spindle", "homing", "spindle"):
        return "continuous"
    # Auto: long recordings are continuous / live spindle style
    if duration_s >= 3.0:
        return "continuous"
    return "impact"


def _finalize_suggestion(
    *,
    fn_hz: float | None,
    flute_count: int,
    min_rpm: float,
    max_rpm: float,
    spindle_freq_hz: float,
    analysis_mode: str,
    base_warnings: list[str],
) -> dict[str, Any]:
    warnings = list(base_warnings)
    if fn_hz is None or fn_hz <= 0:
        return {
            "fn_hz": None,
            "avoid_rpm": [],
            "avoid_rpm_bands": [],
            "suggested_rpm_min": None,
            "suggested_rpm_max": None,
            "n_avoid_in_range": 0,
            "suggestion_confidence": "none",
            "warnings": warnings + (["no_confident_modal_fn"] if "no_confident_modal_fn" not in warnings else []),
        }

    if _near_spindle(fn_hz, spindle_freq_hz):
        warnings.append("fn_near_spindle_operating_frequency")
        # Do not promote spindle 1× as machining fₙ or emit avoid zones
        return {
            "fn_hz": None,
            "avoid_rpm": [],
            "avoid_rpm_bands": [],
            "suggested_rpm_min": None,
            "suggested_rpm_max": None,
            "n_avoid_in_range": 0,
            "suggestion_confidence": "none",
            "warnings": warnings,
            "fn_rejected_as_spindle": True,
            "rejected_fn_hz": float(fn_hz),
        }

    if analysis_mode == "continuous" and _near_spindle(fn_hz, spindle_freq_hz):
        warnings.append("continuous_mode_spindle_dominated")

    avoid = _rpm_to_avoid(fn_hz, flute_count, 5, max_rpm)
    in_range = [r for r in avoid if min_rpm <= r <= max_rpm]
    smin, smax, conf, sw = _suggested_rpm_range(avoid, min_rpm, max_rpm)
    warnings.extend(sw)
    bands = avoid_rpm_bands(fn_hz, flute_count, rpm_min=min_rpm, rpm_max=max_rpm)
    if analysis_mode == "continuous" and conf == "high":
        # Continuous data is weaker evidence for modal ID
        conf = "medium"
        warnings.append("continuous_mode_reduced_confidence")

    return {
        "fn_hz": float(fn_hz),
        "avoid_rpm": avoid,
        "avoid_rpm_bands": bands,
        "suggested_rpm_min": smin,
        "suggested_rpm_max": smax,
        "n_avoid_in_range": len(in_range),
        "suggestion_confidence": conf,
        "warnings": warnings,
        "fn_rejected_as_spindle": False,
    }


def live_spectrum(
    ax: list[float] | np.ndarray,
    ay: list[float] | np.ndarray,
    az: list[float] | np.ndarray,
    sample_rate_hz: float,
    *,
    spindle_freq_hz: float = 400.0,
) -> dict[str, Any]:
    """Rolling FFT summary for live view (highest-variance axis)."""
    ax_a = np.asarray(ax, dtype=float).ravel()
    ay_a = np.asarray(ay, dtype=float).ravel()
    az_a = np.asarray(az, dtype=float).ravel()
    n = int(min(ax_a.size, ay_a.size, az_a.size))
    nyquist = sample_rate_hz / 2.0 if sample_rate_hz > 0 else 0.0
    if n < 8 or sample_rate_hz <= 0:
        return {
            "n_samples": n,
            "fn_hz": None,
            "freqs_hz": [],
            "mag": [],
            "nyquist_hz": nyquist,
            "spindle_freq_hz": spindle_freq_hz,
        }
    data = np.vstack([ax_a[-n:], ay_a[-n:], az_a[-n:]])
    axis_name, signal, _ = highest_variance_axis(data)
    signal = signal - np.mean(signal)
    window = np.hanning(len(signal))
    spec = np.fft.rfft(signal * window)
    freqs = np.fft.rfftfreq(len(signal), d=1.0 / sample_rate_hz)
    mag = np.abs(spec)
    step = max(1, len(freqs) // 128)
    peaks = spectral_peaks(signal, sample_rate_hz, spindle_freq_hz=spindle_freq_hz)
    fn, warns = pick_modal_fn(peaks, spindle_freq_hz, nyquist_hz=nyquist)
    return {
        "n_samples": int(n),
        "fn_hz": fn,
        "freqs_hz": [float(f) for f in freqs[::step]],
        "mag": [float(m) for m in mag[::step]],
        "rms_g": float(np.sqrt(np.mean(signal**2))),
        "peaks": peaks,
        "axis_used": axis_name,
        "nyquist_hz": float(nyquist),
        "spindle_freq_hz": float(spindle_freq_hz),
        "warnings": warns,
    }


def analyze_session_arrays(
    t: np.ndarray,
    data: np.ndarray,
    sample_rate_hz: float,
    *,
    flute_count: int = 4,
    max_rpm: float = 24000.0,
    min_rpm: float = 9000.0,
    spindle_freq_hz: float = 400.0,
    mode: str | None = None,
) -> dict[str, Any]:
    """
    Session-end analysis. Prefers tap_testing.analyze_tap_data when importable;
    otherwise uses a local FFT + tooth-pass avoid RPM estimate.

    Always applies spindle-aware guards and refuses fake full-range suggestions.
    """
    nyquist = float(sample_rate_hz) / 2.0 if sample_rate_hz > 0 else 0.0
    duration_s = float(t[-1] - t[0]) if getattr(t, "size", 0) >= 2 else 0.0
    analysis_mode = _infer_analysis_mode(mode, duration_s)
    axis_name, signal, _ = highest_variance_axis(data) if data.size else ("z", np.asarray([]), 2)
    peaks = spectral_peaks(signal, sample_rate_hz, spindle_freq_hz=spindle_freq_hz) if signal.size else []
    local_fn, pick_warnings = pick_modal_fn(peaks, spindle_freq_hz, nyquist_hz=nyquist)

    engine = "local_fft"
    fn: float | None = local_fn
    unc: float | None = None
    n_teeth = int(flute_count)
    base_warnings = list(pick_warnings)

    try:
        from tap_testing.analyze import analyze_tap_data  # type: ignore

        subtract_bg = analysis_mode == "impact"
        result = analyze_tap_data(
            t,
            data,
            sample_rate_hz,
            flute_count=flute_count,
            max_rpm=max_rpm,
            min_rpm=min_rpm,
            use_axis=axis_name,
            subtract_background=subtract_bg,
            spindle_operating_frequency_hz=spindle_freq_hz,
        )
        engine = "tap_testing"
        cand = float(result.natural_freq_hz)
        unc = result.natural_freq_hz_uncertainty
        n_teeth = int(result.n_teeth_used)
        # Prefer tap_testing fn only if it is not the spindle line; else keep local modal pick
        if _near_spindle(cand, spindle_freq_hz):
            base_warnings.append("tap_testing_fn_near_spindle_overridden")
            fn = local_fn
        else:
            fn = cand
    except Exception:
        pass

    suggestion = _finalize_suggestion(
        fn_hz=fn,
        flute_count=n_teeth,
        min_rpm=min_rpm,
        max_rpm=max_rpm,
        spindle_freq_hz=spindle_freq_hz,
        analysis_mode=analysis_mode,
        base_warnings=base_warnings,
    )

    # Attach band uncertainty when available
    if suggestion["fn_hz"] and unc is not None and suggestion["avoid_rpm_bands"]:
        suggestion["avoid_rpm_bands"] = avoid_rpm_bands(
            float(suggestion["fn_hz"]),
            n_teeth,
            rpm_min=min_rpm,
            rpm_max=max_rpm,
            fn_uncertainty_hz=float(unc),
        )

    damping_ratio = _estimate_damping_ratio(
        t,
        data,
        suggestion["fn_hz"],
        sample_rate_hz,
        analysis_mode=analysis_mode,
    )
    shaping = input_shaping_recommendation(suggestion["fn_hz"], damping_ratio=damping_ratio)

    return {
        "source": "jarvis",
        "engine": engine,
        "fn_hz": suggestion["fn_hz"],
        "fn_hz_uncertainty": unc,
        "avoid_rpm": suggestion["avoid_rpm"],
        "avoid_rpm_bands": suggestion["avoid_rpm_bands"],
        "suggested_rpm_min": suggestion["suggested_rpm_min"],
        "suggested_rpm_max": suggestion["suggested_rpm_max"],
        "n_teeth": n_teeth,
        "sample_rate_hz": float(sample_rate_hz),
        "nyquist_hz": nyquist,
        "spindle_freq_hz": float(spindle_freq_hz),
        "reference_rpm": float(spindle_freq_hz) * 60.0,
        "axis_used": axis_name,
        "analysis_mode": analysis_mode,
        "peaks": peaks,
        "n_avoid_in_range": suggestion["n_avoid_in_range"],
        "suggestion_confidence": suggestion["suggestion_confidence"],
        "warnings": suggestion["warnings"],
        "fn_rejected_as_spindle": suggestion.get("fn_rejected_as_spindle", False),
        "input_shaping": shaping,
        "disclaimer": (
            "Predicted tooth-pass resonance risk from response spectrum; "
            "not a full stability-lobe or safety determination. "
            f"Spindle reference {spindle_freq_hz:g} Hz ({spindle_freq_hz * 60:g} RPM); "
            f"Nyquist {nyquist:g} Hz at {sample_rate_hz:g} S/s."
        ),
    }


def _slice_arrays(
    t: np.ndarray,
    data: np.ndarray,
    t0: float,
    t1: float,
) -> tuple[np.ndarray, np.ndarray]:
    if t.size == 0:
        return t, data
    mask = (t >= t0) & (t <= t1)
    if not np.any(mask):
        return np.asarray([]), np.zeros((3, 0))
    return t[mask], data[:, mask]


def _window_metrics(
    ax: np.ndarray,
    ay: np.ndarray,
    az: np.ndarray,
    sample_rate_hz: float,
    fn_hz: float | None,
) -> dict[str, float]:
    n = min(len(ax), len(ay), len(az))
    if n < 4:
        return {"rms_g": 0.0, "peak_g": 0.0, "crest": 0.0, "resonance_band_energy": 0.0}
    mag = np.sqrt(ax[:n] ** 2 + ay[:n] ** 2 + az[:n] ** 2)
    rms = float(np.sqrt(np.mean(mag**2)))
    peak = float(np.max(np.abs(mag)))
    crest = float(peak / rms) if rms > 1e-12 else 0.0
    band_e = 0.0
    if fn_hz and fn_hz > 0 and sample_rate_hz > 0 and n >= 16:
        x = mag - np.mean(mag)
        window = np.hanning(len(x))
        spec = np.fft.rfft(x * window)
        freqs = np.fft.rfftfreq(len(x), d=1.0 / sample_rate_hz)
        mag_s = np.abs(spec) ** 2
        half = max(5.0, fn_hz * 0.05)
        band = (freqs >= fn_hz - half) & (freqs <= fn_hz + half)
        total = float(np.sum(mag_s[1:])) or 1.0
        band_e = float(np.sum(mag_s[band]) / total)
    return {
        "rms_g": rms,
        "peak_g": peak,
        "crest": crest,
        "resonance_band_energy": band_e,
    }


def _rank_rpm_buckets(
    buckets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Relative good (low score) / bad (high score) observed RPM buckets."""
    scored = [b for b in buckets if b.get("n_windows", 0) >= 1 and b.get("score") is not None]
    if not scored:
        return [], []
    ordered = sorted(scored, key=lambda b: float(b["score"]))
    n = len(ordered)
    k = max(1, n // 4)
    good = ordered[:k]
    bad = list(reversed(ordered[-k:]))
    for g in good:
        g["rank"] = "good"
    for b in bad:
        b["rank"] = "bad"
    return good, bad


def analyze_segment(
    t: np.ndarray,
    data: np.ndarray,
    sample_rate_hz: float,
    *,
    t0: float,
    t1: float,
    flute_count: int = 4,
    max_rpm: float = 24000.0,
    min_rpm: float = 9000.0,
    spindle_freq_hz: float = 400.0,
    mode: str | None = None,
    rpm_samples: list[dict[str, Any]] | None = None,
    reference_rpm: float | None = None,
    reference_rpm_source: str | None = None,
    tool_guid: str | None = None,
    tool_number: int | None = None,
    tool: dict[str, Any] | None = None,
    material: str | None = None,
    segment_id: str | None = None,
    transition_windows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Analyze one time segment: spectrum, predicted avoid bands, optional observed RPM buckets,
    and Fusion-preset cutting-force envelope when the tool has feed/chipload.

    Observed rankings use measured/manual samples only, excluding transition windows
    (tool change / spindle start-stop). Commanded Modbus remains contextual.
    """
    ts, ds = _slice_arrays(t, data, t0, t1)
    base: dict[str, Any] = {
        "segment_id": segment_id,
        "t0": float(t0),
        "t1": float(t1),
        "n_samples": int(ds.shape[1]) if ds.size else 0,
        "tool_guid": tool_guid,
        "tool_number": tool_number,
        "n_teeth": flute_count,
        "reference_rpm": reference_rpm,
        "reference_rpm_source": reference_rpm_source,
        "has_observed_rpm": False,
        "transition_windows": list(transition_windows or []),
        "disclaimer": (
            "Predicted tooth-pass resonance risk from response spectrum; "
            "observed good/bad bands require time-aligned measured RPM telemetry. "
            "Samples inside tool-change / spindle start-stop windows are excluded "
            "from observed rankings. Not a full stability-lobe or safety determination."
        ),
    }
    if ds.size < 6 or sample_rate_hz <= 0:
        base["analysis"] = None
        base["error"] = "insufficient_samples"
        base["cutting_force"] = None
        return base

    analysis = analyze_session_arrays(
        ts,
        ds,
        sample_rate_hz,
        flute_count=flute_count,
        max_rpm=max_rpm,
        min_rpm=min_rpm,
        spindle_freq_hz=spindle_freq_hz,
        mode=mode,
    )
    base["analysis"] = analysis

    # Cutting force from Fusion presets (not invented from material registry)
    force_rpm = None
    if reference_rpm and float(reference_rpm) > 0:
        force_rpm = float(reference_rpm)
    force_out = cutting_force_envelope(
        tool,
        rpm=force_rpm,
        material=material,
        spindle_freq_hz=spindle_freq_hz,
    )
    base["cutting_force"] = force_out.get("cutting_force")
    base["cutting_force_inputs"] = force_out.get("inputs")
    base["cutting_force_warnings"] = force_out.get("warnings") or []

    samples = list(rpm_samples or [])
    observed_raw = [
        s
        for s in samples
        if is_observed_source(s.get("source"))
        and t0 <= float(s.get("t_s", -1)) <= t1
        and float(s.get("rpm") or 0) > 0
    ]
    observed = filter_observed_outside_transitions(observed_raw, transition_windows)
    base["n_observed_rpm_in_transitions"] = max(0, len(observed_raw) - len(observed))
    if not observed and reference_rpm and is_observed_source(reference_rpm_source):
        # Manual / measured segment reference — only if midpoint is outside transitions
        mid = (t0 + t1) / 2.0
        mid_sample = [
            {
                "t_s": mid,
                "rpm": float(reference_rpm),
                "source": reference_rpm_source or "manual",
            }
        ]
        observed = filter_observed_outside_transitions(mid_sample, transition_windows)

    base["has_observed_rpm"] = bool(observed)
    buckets: list[dict[str, Any]] = []
    if observed and ts.size >= 16:
        window_s = max(0.25, min(2.0, (t1 - t0) / 8.0 if t1 > t0 else 0.5))
        fn = float(analysis.get("fn_hz") or 0)
        acc: dict[int, list[dict[str, float]]] = {}
        for s in observed:
            mid = float(s["t_s"])
            w0, w1 = mid - window_s / 2, mid + window_s / 2
            wt, wd = _slice_arrays(ts, ds, w0, w1)
            if wd.size < 8:
                continue
            m = _window_metrics(wd[0], wd[1], wd[2], sample_rate_hz, fn if fn > 0 else None)
            score = m["rms_g"] * (1.0 + 2.0 * m["resonance_band_energy"])
            m["score"] = score
            bucket_key = int(round(float(s["rpm"]) / 500.0) * 500)
            acc.setdefault(bucket_key, []).append(m)

        for key, rows in sorted(acc.items()):
            n_w = len(rows)
            avg_rms = float(np.mean([r["rms_g"] for r in rows]))
            avg_band = float(np.mean([r["resonance_band_energy"] for r in rows]))
            avg_score = float(np.mean([r["score"] for r in rows]))
            confidence = min(1.0, n_w / 8.0)
            buckets.append(
                {
                    "rpm_center": float(key),
                    "rpm_min": float(key - 250),
                    "rpm_max": float(key + 250),
                    "n_windows": n_w,
                    "rms_g": avg_rms,
                    "resonance_band_energy": avg_band,
                    "score": avg_score,
                    "confidence": confidence,
                    "provenance": "observed",
                }
            )

    good, bad = _rank_rpm_buckets(buckets)
    base["rpm_buckets"] = buckets
    base["observed_good_rpm"] = good
    base["observed_bad_rpm"] = bad

    if reference_rpm and reference_rpm > 0:
        bands = analysis.get("avoid_rpm_bands") or []
        in_risk = any(
            float(b["rpm_min"]) <= float(reference_rpm) <= float(b["rpm_max"]) for b in bands
        )
        base["reference_rpm_risk"] = {
            "rpm": float(reference_rpm),
            "source": reference_rpm_source or "unknown",
            "in_avoid_band": in_risk,
            "is_observed_evidence": is_observed_source(reference_rpm_source),
        }

    return base


def analyze_segments(
    t: np.ndarray,
    data: np.ndarray,
    sample_rate_hz: float,
    segments: list[dict[str, Any]],
    *,
    default_flutes: int = 4,
    max_rpm: float = 24000.0,
    min_rpm: float = 9000.0,
    spindle_freq_hz: float = 400.0,
    mode: str | None = None,
    rpm_samples: list[dict[str, Any]] | None = None,
    get_tool: Callable[[str], dict[str, Any] | None] | None = None,
    material: str | None = None,
    transition_windows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Run analyze_segment for each segment dict."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        flutes = seg.get("flutes")
        if flutes is None:
            flutes = default_flutes
        try:
            flutes_i = int(flutes)
        except (TypeError, ValueError):
            flutes_i = default_flutes
        if flutes_i <= 0:
            flutes_i = default_flutes
        tool = None
        guid = seg.get("tool_guid")
        if guid and get_tool is not None:
            tool = get_tool(str(guid))
        out.append(
            analyze_segment(
                t,
                data,
                sample_rate_hz,
                t0=float(seg.get("t0", 0)),
                t1=float(seg.get("t1", 0)),
                flute_count=flutes_i,
                max_rpm=max_rpm,
                min_rpm=min_rpm,
                spindle_freq_hz=spindle_freq_hz,
                mode=mode or seg.get("mode"),
                rpm_samples=rpm_samples,
                reference_rpm=seg.get("reference_rpm"),
                reference_rpm_source=seg.get("reference_rpm_source"),
                tool_guid=seg.get("tool_guid"),
                tool_number=seg.get("tool_number"),
                tool=tool,
                material=material or seg.get("material"),
                segment_id=seg.get("segment_id"),
                transition_windows=transition_windows,
            )
        )
    return out
