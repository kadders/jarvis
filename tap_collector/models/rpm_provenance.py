"""Canonical RPM value with provenance for tap-collector analytics."""

from __future__ import annotations

from typing import Any, Literal

RpmSource = Literal[
    "measured_modbus",
    "commanded_modbus",
    "arborctl",
    "commanded_rrf",
    "rrf_spindle",
    "fusion_reference",
    "manual",
    "estimated",
    "unknown",
]

# Sources that count as observed evidence for good/bad RPM ranking.
# Commanded Modbus / RRF active are contextual (setpoint) — not measured evidence.
OBSERVED_SOURCES: frozenset[str] = frozenset({"measured_modbus", "arborctl", "manual"})

# Sources that are reference/estimate only (theoretical risk, not measured evidence).
REFERENCE_SOURCES: frozenset[str] = frozenset(
    {
        "commanded_modbus",
        "commanded_rrf",
        "rrf_spindle",
        "fusion_reference",
        "estimated",
        "unknown",
    }
)

# RPM-over-time plot series.
MEASURED_PLOT_SOURCES: frozenset[str] = frozenset({"measured_modbus", "arborctl"})
COMMANDED_PLOT_SOURCES: frozenset[str] = frozenset({"commanded_modbus", "commanded_rrf"})


def make_rpm_sample(
    t_s: float,
    rpm: float,
    *,
    source: RpmSource | str = "unknown",
    raw_value: float | None = None,
    scaling: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Build a typed RPM sample dict."""
    return {
        "t_s": float(t_s),
        "rpm": float(rpm),
        "source": str(source),
        "raw_value": float(raw_value) if raw_value is not None else None,
        "scaling": scaling,
        "note": note,
        "is_observed": str(source) in OBSERVED_SOURCES,
    }


def is_observed_source(source: str | None) -> bool:
    return str(source or "") in OBSERVED_SOURCES


def fusion_ref_at_time(t_s: float, segments: list[dict[str, Any]]) -> float | None:
    """Return the covering segment's reference_rpm, mirroring SPA fusionRefAtTime."""
    try:
        t = float(t_s)
    except (TypeError, ValueError):
        return None
    for seg in segments or []:
        try:
            t0 = float(seg.get("t0"))
            t1 = float(seg.get("t1"))
        except (TypeError, ValueError):
            continue
        if t < t0 or t > t1:
            continue
        rpm = seg.get("reference_rpm")
        if rpm is None:
            return None
        try:
            rpm_f = float(rpm)
        except (TypeError, ValueError):
            return None
        return rpm_f if rpm_f > 0 else None
    return None


def fusion_ref_series(
    ts: list[float], segments: list[dict[str, Any]]
) -> list[float | None]:
    """Stepped Fusion/tooling reference series for RPM telemetry charts."""
    return [fusion_ref_at_time(t, segments) for t in ts]
