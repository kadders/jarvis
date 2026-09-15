"""Fusion cutting-preset normalization and force envelope regressions."""

from __future__ import annotations

import math

from models.cutting_force import (
    DOC_MAX_MM,
    DOC_SWEEP_MM,
    WOC_SWEEP_MM,
    cutting_force_envelope,
    estimate_point,
)
from models.tool_library import normalize_fusion_tool


def _fusion_tool(**preset_extra):
    return {
        "guid": "guid-force-1",
        "description": "6mm 3FL",
        "unit": "millimeters",
        "geometry": {"DC": 6.0, "NOF": 3},
        "start-values": {
            "presets": [
                {
                    "name": "Default preset",
                    "v_f": 1350.0,
                    "f_z": 0.025,
                    "n": 18000.0,
                    **preset_extra,
                }
            ]
        },
    }


def test_normalize_nested_vf_fz_n():
    rec = normalize_fusion_tool(_fusion_tool())
    assert rec["feed_mm_min"] == 1350.0
    assert abs(rec["chipload_mm"] - 0.025) < 1e-9
    assert rec["reference_rpm"] == 18000.0
    assert rec["cutting_preset_name"] == "Default preset"
    assert rec["diameter_mm"] == 6.0
    assert rec["flutes"] == 3


def test_normalize_english_aliases_and_inch_units():
    raw = {
        "guid": "guid-inch",
        "description": "1/4 EM",
        "unit": "inches",
        "geometry": {"DC": 0.25, "NOF": 4},
        "start-values": {
            "presets": [
                {
                    "feedRate": 50.0,  # inch/min → 1270 mm/min
                    "feedPerTooth": 0.001,  # inch → 0.0254 mm
                    "spindleSpeed": 16500,
                }
            ]
        },
    }
    rec = normalize_fusion_tool(raw)
    assert abs(rec["feed_mm_min"] - 50.0 * 25.4) < 1e-6
    assert abs(rec["chipload_mm"] - 0.0254) < 1e-6
    assert rec["reference_rpm"] == 16500
    assert abs(rec["diameter_mm"] - 6.35) < 1e-6


def test_missing_preset_no_fabricated_force():
    tool = normalize_fusion_tool(
        {
            "guid": "g2",
            "unit": "millimeters",
            "geometry": {"DC": 6, "NOF": 3},
            "start-values": {"presets": [{"n": 18000}]},
        }
    )
    out = cutting_force_envelope(tool, rpm=18000)
    assert out["cutting_force"] is None
    assert "missing_tool_cutting_preset" in out["warnings"]


def test_force_envelope_grid_and_doc_cap():
    tool = normalize_fusion_tool(_fusion_tool())
    out = cutting_force_envelope(tool, rpm=18000, material="6061 aluminum")
    cf = out["cutting_force"]
    assert cf is not None
    assert len(cf["points"]) == len(WOC_SWEEP_MM) * len(DOC_SWEEP_MM)
    assert max(p["doc_mm"] for p in cf["points"]) <= DOC_MAX_MM
    assert cf["doc_max_mm"] == 4.5
    # Reject DoC above 4.5 via estimate_point
    assert (
        estimate_point(
            diameter_mm=6,
            n_teeth=3,
            rpm=18000,
            feed_mm_min=1350,
            width_of_cut_mm=1.5,
            depth_of_cut_mm=5.0,
        )
        is None
    )


def test_feed_chipload_mismatch_warning():
    # Feed implies fz = 1350/(3*18000)=0.025; set fusion chipload far away
    tool = normalize_fusion_tool(_fusion_tool(f_z=0.05))
    out = cutting_force_envelope(tool, rpm=18000)
    assert "feed_chipload_mismatch" in out["warnings"]
    pt = out["cutting_force"]["points"][0]
    assert pt["fz_mismatch_pct"] is not None
    assert abs(pt["fz_mismatch_pct"]) > 25


def test_numerical_parity_with_tap_testing_slotting_scale():
    """Match tap_testing.feeds_speeds mean Ft for partial WoC."""
    try:
        from tap_testing.feeds_speeds import spindle_force_and_torque
    except ImportError:
        # Image/local env without sibling package — verify internal formula only
        pt = estimate_point(
            diameter_mm=6.0,
            n_teeth=3,
            rpm=18000.0,
            feed_mm_min=1350.0,
            width_of_cut_mm=1.5,
            depth_of_cut_mm=3.0,
            material="6061 aluminum",
        )
        assert pt is not None
        assert pt["tangential_force_n"] > 0
        assert pt["power_w"] > 0
        return

    ref = spindle_force_and_torque(
        6.0,
        3,
        3.0,
        18000.0,
        feed_mm_min=1350.0,
        width_of_cut_mm=1.5,
        material="6061 aluminum",
    )
    pt = estimate_point(
        diameter_mm=6.0,
        n_teeth=3,
        rpm=18000.0,
        feed_mm_min=1350.0,
        width_of_cut_mm=1.5,
        depth_of_cut_mm=3.0,
        material="6061 aluminum",
    )
    assert pt is not None
    assert math.isclose(pt["tangential_force_n"], ref.tangential_force_N, rel_tol=1e-6)
    assert math.isclose(pt["torque_nm"], ref.torque_Nm, rel_tol=1e-6)
    assert math.isclose(pt["power_w"], ref.power_W, rel_tol=1e-6)
    assert math.isclose(pt["resultant_force_n"], ref.resultant_force_N, rel_tol=1e-6)
    assert math.isclose(pt["mrr_mm3_per_min"], ref.mrr_mm3_per_min, rel_tol=1e-6)
