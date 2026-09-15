"""HUD overlay state from MQTT (video_overlay + optional accel/batch fallback)."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def format_axis_bar(val: float | None, *, scale: float = 2.0, width: int = 16) -> str:
    if val is None:
        return " " * width
    scale = max(0.1, float(scale))
    half = width // 2
    idx = int(round((float(val) / scale) * half))
    idx = max(-half, min(half, idx))
    bar = [" "] * width
    bar[half] = "|"
    if idx > 0:
        for i in range(half + 1, half + 1 + idx):
            if i < width:
                bar[i] = "█"
    elif idx < 0:
        for i in range(half + idx, half):
            if i >= 0:
                bar[i] = "█"
    return "".join(bar)


def _is_accel_line(line: str) -> bool:
    s = line.strip()
    if s.startswith("|a|="):
        return True
    if len(s) >= 2 and s[0] in "XYZ" and s[1] == " ":
        return True
    return False


def _overlay_contains_accel(lines: list[str]) -> bool:
    return any(_is_accel_line(line) for line in lines)


def _strip_accel_block(lines: list[str]) -> list[str]:
    """Remove contiguous |a|= / X/Y/Z bar lines (may be followed by job filename)."""
    if not lines:
        return []
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _is_accel_line(lines[i]):
            while i < len(lines) and _is_accel_line(lines[i]):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return out


@dataclass
class HudState:
    session_id: str = ""
    text: str = ""
    updated_at: float = 0.0
    ax_g: float | None = None
    ay_g: float | None = None
    az_g: float | None = None
    accel_updated_at: float = 0.0
    accel_bar_scale_g: float = 2.0
    _lines: list[str] = field(default_factory=list)

    def apply_video_overlay(self, payload: dict[str, Any]) -> None:
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        sid = str(payload.get("session_id") or self.session_id or "")
        if sid:
            self.session_id = sid
        self.text = text
        self._lines = text.splitlines()
        self.updated_at = time.monotonic()

    def apply_accel_batch(self, payload: dict[str, Any]) -> None:
        ax = payload.get("ax") or []
        ay = payload.get("ay") or []
        az = payload.get("az") or []
        if not ax or not ay or not az:
            return
        self.ax_g = float(ax[-1])
        self.ay_g = float(ay[-1])
        self.az_g = float(az[-1])
        sid = str(payload.get("session_id") or "")
        if sid:
            self.session_id = sid
        self.accel_updated_at = time.monotonic()

    def _accel_lines(self) -> list[str]:
        if self.ax_g is None or self.ay_g is None or self.az_g is None:
            return []
        mag = math.sqrt(self.ax_g ** 2 + self.ay_g ** 2 + self.az_g ** 2)
        scale = self.accel_bar_scale_g
        lines = [f"|a|={mag:.3f}g"]
        for label, val in (("X", self.ax_g), ("Y", self.ay_g), ("Z", self.az_g)):
            lines.append(f"{label} {val:+.3f} [{format_axis_bar(val, scale=scale)}]")
        return lines

    def render_text(self, *, overlay_stale_s: float) -> str:
        now = time.monotonic()
        overlay_fresh = bool(self.text and (now - self.updated_at) <= overlay_stale_s)
        base_lines = self._lines.copy() if self._lines else []
        accel_fresh = (
            self.ax_g is not None
            and (now - self.accel_updated_at) <= overlay_stale_s * 2
        )

        if overlay_fresh and _overlay_contains_accel(base_lines):
            # Pi video_overlay already includes ADXL — do not merge accel/batch.
            return "\n".join(base_lines).strip()

        if not accel_fresh:
            return "\n".join(base_lines).strip()

        if overlay_fresh:
            # RRF-only overlay: append live accel from batch.
            base_lines.extend(self._accel_lines())
            return "\n".join(base_lines).strip()

        # Stale overlay: keep last RRF lines, refresh accel from batch.
        base_lines = _strip_accel_block(base_lines)
        base_lines.extend(self._accel_lines())
        return "\n".join(base_lines).strip()

    def write_overlay_file(self, path: Path, *, overlay_stale_s: float) -> bool:
        text = self.render_text(overlay_stale_s=overlay_stale_s)
        if not text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True

    def overlay_age_s(self) -> float | None:
        if not self.text:
            return None
        return time.monotonic() - self.updated_at

    def reset(self) -> None:
        self.session_id = ""
        self.text = ""
        self.updated_at = 0.0
        self.ax_g = self.ay_g = self.az_g = None
        self.accel_updated_at = 0.0
        self._lines = []
