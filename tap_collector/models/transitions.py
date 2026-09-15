"""Tool-change and spindle start/stop transition windows for RPM analytics."""

from __future__ import annotations

from typing import Any


def _as_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_boolish(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(int(v))
    s = str(v).strip().lower()
    if s in ("1", "true", "on", "yes"):
        return True
    if s in ("0", "false", "off", "no"):
        return False
    return None


def _registers_from_row(row: dict[str, Any]) -> dict[str, Any]:
    regs = row.get("registers")
    if isinstance(regs, dict):
        return regs
    return row


def _commanded_rpm_from_row(row: dict[str, Any], *, poles: float = 2.0) -> float | None:
    """Best-effort commanded RPM from H100 hr_513 (freq×10)."""
    regs = _registers_from_row(row)
    raw = _as_float(regs.get("hr_513"))
    if raw is None or raw <= 0:
        return None
    freq_hz = raw / 10.0
    p = float(poles) if poles and float(poles) > 0 else 2.0
    return float(freq_hz) * 120.0 / p


def _spindle_running_from_row(
    row: dict[str, Any],
    *,
    poles: float = 2.0,
    off_rpm: float = 50.0,
) -> bool | None:
    """
    Infer spindle running state from coils and/or commanded RPM.

    H100: co_73 forward, co_74 reverse, co_75 stop.
    """
    regs = _registers_from_row(row)
    fwd = _as_boolish(regs.get("co_73"))
    rev = _as_boolish(regs.get("co_74"))
    stop = _as_boolish(regs.get("co_75"))
    has_coil = any(k in regs for k in ("co_73", "co_74", "co_75"))
    if has_coil:
        if stop is True:
            return False
        if fwd is True or rev is True:
            return True
        # Direction/stop coils present but not commanding run → off
        return False
    cmd = _commanded_rpm_from_row(row, poles=poles)
    if cmd is not None:
        return cmd >= float(off_rpm)
    return None


def _selection_tool_events(tool_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in tool_events or []:
        if not isinstance(ev, dict):
            continue
        et = str(ev.get("event_type") or "").lower()
        event = str(ev.get("event") or "").lower()
        if et in ("tool_selected", "tool_changed", "start_session") or event in (
            "change",
            "start",
            "select",
        ):
            t_s = _as_float(ev.get("t_s"))
            if t_s is None or t_s < 0:
                continue
            out.append(ev)
    return out


def _append_spindle_edge(
    windows: list[dict[str, Any]],
    *,
    t_s: float,
    running: bool,
    half_width_s: float,
) -> None:
    kind = "spindle_start" if running else "spindle_stop"
    windows.append(
        {
            "t0": t_s - half_width_s,
            "t1": t_s + half_width_s,
            "kind": kind,
            "tool_number": None,
            "tool_guid": None,
            "t_event": t_s,
        }
    )


def build_transition_windows(
    tool_events: list[dict[str, Any]] | None,
    modbus_rows: list[dict[str, Any]] | None,
    *,
    segments: list[dict[str, Any]] | None = None,
    rpm_samples: list[dict[str, Any]] | None = None,
    half_width_s: float = 0.75,
    poles: float = 2.0,
    spindle_off_rpm: float = 50.0,
) -> list[dict[str, Any]]:
    """
    Build session-level transition windows for excluding RPM from observed rankings.

    Windows are inclusive [t0, t1] around tool changes and spindle start/stop edges.
    """
    hw = max(0.05, float(half_width_s))
    windows: list[dict[str, Any]] = []

    for ev in _selection_tool_events(list(tool_events or [])):
        t_s = float(ev["t_s"])
        tn = ev.get("tool_number")
        try:
            tn_i = int(tn) if tn is not None else None
        except (TypeError, ValueError):
            tn_i = None
        windows.append(
            {
                "t0": t_s - hw,
                "t1": t_s + hw,
                "kind": "tool_change",
                "tool_number": tn_i,
                "tool_guid": ev.get("tool_guid"),
                "t_event": t_s,
            }
        )

    # Also mark segment open boundaries when tool events were sparse
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        t0 = _as_float(seg.get("t0"))
        if t0 is None:
            continue
        # Avoid duplicating near an existing tool_change window
        if any(
            w["kind"] == "tool_change" and abs(float(w.get("t_event", -1e9)) - t0) < hw * 0.5
            for w in windows
        ):
            continue
        # Only add if this looks like a tool boundary (has tool_number)
        if seg.get("tool_number") is None and not seg.get("tool_guid"):
            continue
        windows.append(
            {
                "t0": t0 - hw,
                "t1": t0 + hw,
                "kind": "tool_change",
                "tool_number": seg.get("tool_number"),
                "tool_guid": seg.get("tool_guid"),
                "t_event": t0,
            }
        )

    spindle_edges = 0
    prev_running: bool | None = None
    for row in modbus_rows or []:
        if not isinstance(row, dict):
            continue
        t_s = _as_float(row.get("t_s"))
        if t_s is None or t_s < 0:
            continue
        running = _spindle_running_from_row(
            row, poles=poles, off_rpm=spindle_off_rpm
        )
        if running is None:
            continue
        if prev_running is None:
            prev_running = running
            continue
        if running == prev_running:
            continue
        _append_spindle_edge(windows, t_s=t_s, running=running, half_width_s=hw)
        spindle_edges += 1
        prev_running = running

    # Fallback: commanded RPM crossing off threshold (CSV import without coils)
    if spindle_edges == 0 and rpm_samples:
        prev_on: bool | None = None
        for s in sorted(rpm_samples, key=lambda x: float(x.get("t_s") or 0.0)):
            if str(s.get("source")) != "commanded_modbus":
                continue
            t_s = _as_float(s.get("t_s"))
            rpm = _as_float(s.get("rpm"))
            if t_s is None or rpm is None:
                continue
            on = rpm >= float(spindle_off_rpm)
            if prev_on is None:
                prev_on = on
                continue
            if on == prev_on:
                continue
            _append_spindle_edge(windows, t_s=t_s, running=on, half_width_s=hw)
            prev_on = on

    windows.sort(key=lambda w: float(w.get("t0") or 0.0))
    return windows


def t_in_transition(
    t_s: float,
    windows: list[dict[str, Any]] | None,
) -> bool:
    if not windows:
        return False
    t = float(t_s)
    for w in windows:
        try:
            if float(w["t0"]) <= t <= float(w["t1"]):
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def filter_observed_outside_transitions(
    samples: list[dict[str, Any]],
    windows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep samples whose t_s is outside all transition windows."""
    if not windows:
        return list(samples)
    return [s for s in samples if not t_in_transition(float(s.get("t_s", -1)), windows)]
