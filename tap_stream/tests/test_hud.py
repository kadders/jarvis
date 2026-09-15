"""Tests for tap_stream HUD overlay handling."""

from __future__ import annotations

import time

from hud import HudState, _overlay_contains_accel, _strip_accel_block


def test_hud_apply_video_overlay():
    hud = HudState()
    hud.apply_video_overlay(
        {
            "session_id": "sess1",
            "t_s": 1.0,
            "text": "t=1.0s\nRPM 24000",
        }
    )
    assert hud.session_id == "sess1"
    assert "RPM 24000" in hud.render_text(overlay_stale_s=1.0)


def test_hud_accel_batch_fallback_no_accel_in_overlay():
    """Fresh overlay without ADXL lines gets accel appended from batch."""
    hud = HudState()
    hud.apply_video_overlay({"session_id": "sess1", "text": "tool line\nRPM 24000"})
    hud.apply_accel_batch({"ax": [0.1], "ay": [0.0], "az": [1.0]})
    text = hud.render_text(overlay_stale_s=10.0)
    assert "RPM 24000" in text
    assert "|a|=" in text
    assert "X +0.100" in text


def test_hud_no_duplicate_accel_when_overlay_has_pi_format_and_job_line():
    """Pi overlay includes ADXL + trailing job filename; batch must not duplicate."""
    hud = HudState()
    pi_text = (
        "t=12.4s  job=0:05:00\n"
        "T1 Endmill\n"
        "RPM 24000  Load 23.3%\n"
        "|a|=0.975g\n"
        "X +0.044 [        |        ]\n"
        "Y +0.001 [        |        ]\n"
        "Z +0.998 [        |████████]\n"
        "part.gcode"
    )
    hud.apply_video_overlay({"session_id": "sess1", "text": pi_text})
    hud.apply_accel_batch({"ax": [0.5], "ay": [0.0], "az": [1.0]})
    text = hud.render_text(overlay_stale_s=10.0)
    assert text.count("|a|=") == 1
    assert text.count("part.gcode") == 1
    assert "RPM 24000" in text


def test_hud_stale_overlay_refreshes_accel_from_batch():
    hud = HudState()
    hud.apply_video_overlay(
        {
            "text": "RPM 24000\n|a|=0.100g\nX +0.010 [x]\nY +0.000 [x]\nZ +1.000 [x]",
        }
    )
    time.sleep(0.02)
    hud.apply_accel_batch({"ax": [0.2], "ay": [0.0], "az": [1.0]})
    text = hud.render_text(overlay_stale_s=0.001)
    assert "RPM 24000" in text
    assert "|a|=" in text
    assert "X +0.200" in text


def test_strip_accel_block_with_trailing_job():
    lines = [
        "RPM 24000",
        "|a|=1.0g",
        "X +0.1 [x]",
        "Y +0.0 [x]",
        "Z +1.0 [x]",
        "part.gcode",
    ]
    stripped = _strip_accel_block(lines)
    assert stripped == ["RPM 24000", "part.gcode"]


def test_overlay_contains_accel():
    assert _overlay_contains_accel(["|a|=1.0g", "job.gcode"])
    assert not _overlay_contains_accel(["RPM 24000", "job.gcode"])
