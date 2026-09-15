"""Cutting force / MRR estimates from Fusion tool presets + WoC/DoC envelope."""

from __future__ import annotations

import math
from typing import Any

# WoC / DoC envelope (mm) from plan
WOC_SWEEP_MM = (1.2, 1.35, 1.5)
DOC_SWEEP_MM = (1.5, 3.0, 4.5)
DOC_MAX_MM = 4.5
DEFAULT_SPINDLE_POWER_W = 1500.0

# Specific cutting coefficients (N/mm²) — same registry as tap-testing material.py
MATERIALS: dict[str, dict[str, float]] = {
    "6061 aluminum": {"ks": 791.0, "beta_deg": 71.6},
    "7075 aluminum": {"ks": 900.0, "beta_deg": 70.0},
    "A36 steel": {"ks": 1500.0, "beta_deg": 75.0},
}
DEFAULT_MATERIAL = "6061 aluminum"


def cutting_coefficients(material: str | None = None) -> tuple[float, float, str]:
    name = (material or DEFAULT_MATERIAL).strip()
    if name not in MATERIALS:
        name = DEFAULT_MATERIAL
    m = MATERIALS[name]
    beta = math.radians(m["beta_deg"])
    ks = m["ks"]
    kt = ks * math.sin(beta)
    kn = ks * math.cos(beta)
    return kt, kn, name


def list_materials() -> list[str]:
    return sorted(MATERIALS.keys())


def estimate_point(
    *,
    diameter_mm: float,
    n_teeth: int,
    rpm: float,
    feed_mm_min: float,
    width_of_cut_mm: float,
    depth_of_cut_mm: float,
    fusion_chipload_mm: float | None = None,
    material: str | None = None,
) -> dict[str, Any] | None:
    """Single WoC/DoC force estimate. Returns None if inputs invalid."""
    if (
        diameter_mm <= 0
        or n_teeth <= 0
        or rpm <= 0
        or feed_mm_min <= 0
        or width_of_cut_mm <= 0
        or depth_of_cut_mm <= 0
    ):
        return None
    if depth_of_cut_mm > DOC_MAX_MM + 1e-9:
        return None

    kt, kn, mat_name = cutting_coefficients(material)
    fz_eff = feed_mm_min / (n_teeth * rpm)
    b = depth_of_cut_mm
    w = min(width_of_cut_mm, diameter_mm)
    engagement = w / diameter_mm if diameter_mm > 0 else 1.0

    # Slotting mean Ft scaled by radial engagement (mirror tap_testing.feeds_speeds)
    ft_mean_slotting = n_teeth * (2.0 / math.pi) * kt * b * fz_eff
    fx_mean = n_teeth * b * (kn / 4.0) * fz_eff
    fy_mean = n_teeth * b * (kt / 4.0) * fz_eff
    resultant_slotting = math.sqrt(fx_mean * fx_mean + fy_mean * fy_mean)
    ft_mean = ft_mean_slotting * engagement
    resultant = resultant_slotting * engagement

    r_m = (diameter_mm / 2.0) / 1000.0
    torque_nm = ft_mean * r_m
    omega = 2.0 * math.pi * rpm / 60.0
    power_w = torque_nm * omega
    mrr = w * b * feed_mm_min

    mismatch = None
    if fusion_chipload_mm and fusion_chipload_mm > 0:
        mismatch = 100.0 * (fz_eff - fusion_chipload_mm) / fusion_chipload_mm

    return {
        "woc_mm": float(w),
        "doc_mm": float(b),
        "rpm": float(rpm),
        "feed_mm_min": float(feed_mm_min),
        "fz_effective_mm": float(fz_eff),
        "fz_fusion_mm": float(fusion_chipload_mm) if fusion_chipload_mm else None,
        "fz_mismatch_pct": float(mismatch) if mismatch is not None else None,
        "tangential_force_n": float(ft_mean),
        "resultant_force_n": float(resultant),
        "torque_nm": float(torque_nm),
        "power_w": float(power_w),
        "mrr_mm3_per_min": float(mrr),
        "material": mat_name,
        "spindle_ok": power_w <= DEFAULT_SPINDLE_POWER_W,
    }


def cutting_force_envelope(
    tool: dict[str, Any] | None,
    *,
    rpm: float | None = None,
    material: str | None = None,
    spindle_freq_hz: float = 400.0,
) -> dict[str, Any]:
    """
    Build WoC×DoC force envelope from Fusion tool presets.

    Requires feed_mm_min from the tool library. Does not invent feed/chipload.
    """
    warnings: list[str] = []
    if not tool:
        return {
            "cutting_force": None,
            "warnings": ["missing_tool_cutting_preset"],
        }

    diameter_mm = tool.get("diameter_mm")
    if diameter_mm is None and tool.get("diameter") is not None:
        # Assume already mm if diameter_mm missing
        try:
            diameter_mm = float(tool["diameter"])
        except (TypeError, ValueError):
            diameter_mm = None
    try:
        n_teeth = int(tool.get("flutes") or 0)
    except (TypeError, ValueError):
        n_teeth = 0

    feed = tool.get("feed_mm_min")
    fz_fusion = tool.get("chipload_mm")
    fusion_rpm = tool.get("reference_rpm")

    if feed is None or float(feed) <= 0:
        return {
            "cutting_force": None,
            "warnings": ["missing_tool_cutting_preset"],
            "inputs": {
                "tool_guid": tool.get("guid"),
                "diameter_mm": diameter_mm,
                "flutes": n_teeth,
                "feed_mm_min": feed,
                "chipload_mm": fz_fusion,
            },
        }

    feed_f = float(feed)
    # RPM priority: caller (observed/commanded) → Fusion preset → spindle max
    rpm_used = None
    rpm_source = None
    if rpm is not None and float(rpm) > 0:
        rpm_used = float(rpm)
        rpm_source = "segment"
    elif fusion_rpm is not None and float(fusion_rpm) > 0:
        rpm_used = float(fusion_rpm)
        rpm_source = "fusion_reference"
    else:
        rpm_used = float(spindle_freq_hz) * 60.0
        rpm_source = "spindle_max"
        warnings.append("rpm_fallback_spindle_max")

    if diameter_mm is None or float(diameter_mm) <= 0 or n_teeth <= 0:
        return {
            "cutting_force": None,
            "warnings": ["missing_tool_geometry"],
            "inputs": {
                "tool_guid": tool.get("guid"),
                "diameter_mm": diameter_mm,
                "flutes": n_teeth,
                "feed_mm_min": feed_f,
                "chipload_mm": fz_fusion,
                "rpm": rpm_used,
                "rpm_source": rpm_source,
            },
        }

    fz_eff = feed_f / (n_teeth * rpm_used)
    if fz_fusion is not None and float(fz_fusion) > 0:
        mismatch = 100.0 * (fz_eff - float(fz_fusion)) / float(fz_fusion)
        if abs(mismatch) > 25.0:
            warnings.append("feed_chipload_mismatch")

    points: list[dict[str, Any]] = []
    for woc in WOC_SWEEP_MM:
        for doc in DOC_SWEEP_MM:
            pt = estimate_point(
                diameter_mm=float(diameter_mm),
                n_teeth=n_teeth,
                rpm=rpm_used,
                feed_mm_min=feed_f,
                width_of_cut_mm=woc,
                depth_of_cut_mm=doc,
                fusion_chipload_mm=float(fz_fusion) if fz_fusion else None,
                material=material,
            )
            if pt:
                points.append(pt)

    max_power = max((p["power_w"] for p in points), default=0.0)
    max_ft = max((p["tangential_force_n"] for p in points), default=0.0)

    return {
        "cutting_force": {
            "points": points,
            "woc_mm": list(WOC_SWEEP_MM),
            "doc_mm": list(DOC_SWEEP_MM),
            "doc_max_mm": DOC_MAX_MM,
            "spindle_power_limit_w": DEFAULT_SPINDLE_POWER_W,
            "max_power_w": max_power,
            "max_tangential_force_n": max_ft,
            "overload": max_power > DEFAULT_SPINDLE_POWER_W,
            "disclaimer": (
                "Engineering approximation from Fusion feed/chipload and material "
                "coefficients; radial engagement scales slotting force by WoC/D. "
                "Not an FRF or machine safety certification."
            ),
        },
        "inputs": {
            "tool_guid": tool.get("guid"),
            "tool_name": tool.get("description") or tool.get("product_id"),
            "diameter_mm": float(diameter_mm),
            "flutes": n_teeth,
            "feed_mm_min": feed_f,
            "chipload_mm": float(fz_fusion) if fz_fusion else None,
            "fz_effective_mm": fz_eff,
            "rpm": rpm_used,
            "rpm_source": rpm_source,
            "material": cutting_coefficients(material)[2],
        },
        "warnings": warnings,
    }
