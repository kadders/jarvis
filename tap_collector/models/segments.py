"""Tool-segment helpers: validation, seeding from tool events, tool resolution."""

from __future__ import annotations

import uuid
from typing import Any, Callable


def new_segment_id() -> str:
    return f"seg-{uuid.uuid4().hex[:10]}"


def validate_segments(
    segments: list[dict[str, Any]],
    *,
    t_min: float | None = None,
    t_max: float | None = None,
) -> list[str]:
    """Return a list of validation error strings (empty if OK)."""
    errors: list[str] = []
    if not isinstance(segments, list):
        return ["segments must be a list"]
    cleaned: list[tuple[float, float, str]] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            errors.append(f"segment[{i}] must be an object")
            continue
        try:
            t0 = float(seg["t0"])
            t1 = float(seg["t1"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"segment[{i}] requires numeric t0 and t1")
            continue
        if t1 <= t0:
            errors.append(f"segment[{i}] t1 must be greater than t0")
            continue
        if t_min is not None and t0 < t_min - 1e-6:
            errors.append(f"segment[{i}] t0 before recording start ({t_min})")
        if t_max is not None and t1 > t_max + 1e-6:
            errors.append(f"segment[{i}] t1 after recording end ({t_max})")
        ref = seg.get("reference_rpm")
        if ref is not None:
            try:
                rpm = float(ref)
                if rpm <= 0 or rpm > 100_000:
                    errors.append(f"segment[{i}] reference_rpm out of range")
            except (TypeError, ValueError):
                errors.append(f"segment[{i}] reference_rpm must be numeric")
        cleaned.append((t0, t1, seg.get("segment_id") or f"#{i}"))

    cleaned.sort(key=lambda x: x[0])
    for i in range(len(cleaned) - 1):
        _, hi, a = cleaned[i]
        lo, _, b = cleaned[i + 1]
        if lo < hi - 1e-9:
            errors.append(f"overlapping segments {a} and {b}")
    return errors


def is_selection_tool_event(ev: dict[str, Any]) -> bool:
    """True when the event should open/close a tool segment (not offset-only)."""
    event = str(ev.get("event") or "").lower()
    et = str(ev.get("event_type") or "").lower()
    if et in ("tool_offset_changed", "tool_table_snapshot"):
        return False
    if et in ("tool_selected", "tool_deselected"):
        return True
    # Legacy events without event_type
    if event == "stop":
        return False
    return event in ("start", "change")


def normalize_segment(seg: dict[str, Any]) -> dict[str, Any]:
    """Normalize a user/API segment into the persisted shape."""
    sid = str(seg.get("segment_id") or new_segment_id())
    out: dict[str, Any] = {
        "segment_id": sid,
        "t0": float(seg["t0"]),
        "t1": float(seg["t1"]),
        "state": seg.get("state") or "closed",
        "tool_number": seg.get("tool_number"),
        "rrf_slot": seg.get("rrf_slot", seg.get("tool_number")),
        "tool_guid": seg.get("tool_guid"),
        "tool_name": seg.get("tool_name"),
        "flutes": seg.get("flutes"),
        "diameter": seg.get("diameter"),
        "diameter_mm": seg.get("diameter_mm"),
        "reference_rpm": seg.get("reference_rpm"),
        "reference_rpm_source": seg.get("reference_rpm_source"),
        "feed_mm_min": seg.get("feed_mm_min"),
        "chipload_mm": seg.get("chipload_mm"),
        "cutting_preset_name": seg.get("cutting_preset_name"),
        "material": seg.get("material"),
        "ambiguous_tools": list(seg.get("ambiguous_tools") or []),
        "manual": bool(seg.get("manual", False)),
        "note": seg.get("note"),
        "tool_snapshot": seg.get("tool_snapshot"),
        "offsets": seg.get("offsets"),
        "n_samples": seg.get("n_samples"),
        "open_event_id": seg.get("open_event_id"),
        "close_event_id": seg.get("close_event_id"),
        "boundary_source": seg.get("boundary_source"),
        "resolution_status": seg.get("resolution_status"),
        "warnings": list(seg.get("warnings") or []),
    }
    # Coerce tool_number
    if out["tool_number"] is not None:
        try:
            out["tool_number"] = int(out["tool_number"])
        except (TypeError, ValueError):
            out["tool_number"] = None
    if out["flutes"] is not None:
        try:
            out["flutes"] = int(out["flutes"])
        except (TypeError, ValueError):
            out["flutes"] = None
    if out["reference_rpm"] is not None:
        try:
            out["reference_rpm"] = float(out["reference_rpm"])
        except (TypeError, ValueError):
            out["reference_rpm"] = None
    for key in ("feed_mm_min", "chipload_mm"):
        if out[key] is not None:
            try:
                out[key] = float(out[key])
            except (TypeError, ValueError):
                out[key] = None
    if out["cutting_preset_name"] is not None:
        out["cutting_preset_name"] = str(out["cutting_preset_name"])
    return out


def seed_segments_from_tool_events(
    tool_events: list[dict[str, Any]],
    *,
    t_end: float,
    t_start: float = 0.0,
) -> list[dict[str, Any]]:
    """
    Build contiguous segments from a tool-change timeline.

    Events with t_s=None are ordered by list position using previous/next times.
    """
    if not tool_events:
        return []

    # Normalize selection events with usable times (skip offset/table snapshots)
    dated: list[dict[str, Any]] = []
    for ev in tool_events:
        if not is_selection_tool_event(ev):
            continue
        t_s = ev.get("t_s")
        try:
            t_val = float(t_s) if t_s is not None else None
        except (TypeError, ValueError):
            t_val = None
        dated.append({**ev, "_t": t_val})

    if not dated:
        return []

    # Fill missing times by linear interpolation of indices
    known = [(i, e["_t"]) for i, e in enumerate(dated) if e["_t"] is not None]
    if not known:
        # Spread evenly across [t_start, t_end]
        n = len(dated)
        for i, e in enumerate(dated):
            e["_t"] = t_start + (t_end - t_start) * (i / max(1, n))
    else:
        # Back-fill before first known
        first_i, first_t = known[0]
        for i in range(first_i):
            dated[i]["_t"] = t_start if first_t is None else min(t_start, first_t)
        # Forward-fill gaps
        last_t = dated[0]["_t"]
        for e in dated:
            if e["_t"] is None:
                e["_t"] = last_t
            else:
                last_t = e["_t"]

    dated.sort(key=lambda e: float(e["_t"]))
    segments: list[dict[str, Any]] = []
    open_ev: dict[str, Any] | None = None
    open_t0: float | None = None

    def _close_open(t1: float) -> None:
        nonlocal open_ev, open_t0
        if open_ev is None or open_t0 is None:
            return
        if t1 <= open_t0:
            open_ev = None
            open_t0 = None
            return
        tn = open_ev.get("tool_number")
        try:
            tn_i = int(tn) if tn is not None else None
        except (TypeError, ValueError):
            tn_i = None
        segments.append(
            normalize_segment(
                {
                    "t0": max(open_t0, t_start),
                    "t1": t1,
                    "state": "closed",
                    "tool_number": tn_i,
                    "rrf_slot": tn_i,
                    "tool_name": open_ev.get("tool_name"),
                    "tool_snapshot": open_ev.get("tool_snapshot"),
                    "offsets": open_ev.get("offsets"),
                    "open_event_id": open_ev.get("event_id"),
                    "boundary_source": "tool_events",
                    "manual": False,
                }
            )
        )
        open_ev = None
        open_t0 = None

    for ev in dated:
        t0 = float(ev["_t"])
        et = str(ev.get("event_type") or "").lower()
        tn = ev.get("tool_number")
        try:
            tn_i = int(tn) if tn is not None else None
        except (TypeError, ValueError):
            tn_i = None
        # Deselect / negative slot closes the active tool and leaves a gap
        # (matches live _on_tool behavior).
        is_gap = et == "tool_deselected" or (tn_i is not None and tn_i < 0)
        if open_ev is not None:
            _close_open(t0)
        if is_gap:
            continue
        open_ev = ev
        open_t0 = t0

    end = float(t_end)
    if open_ev is not None and open_t0 is not None:
        close_t = end if end > open_t0 else open_t0 + 1.0
        _close_open(close_t)
    return segments


def resolve_segment_tools(
    segments: list[dict[str, Any]],
    *,
    lookup_by_number: Callable[[int], list[dict[str, Any]]],
    get_tool: Callable[[str], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """
    Attach Fusion GUID / flutes / reference RPM when unambiguous.

    Ambiguous tool numbers leave tool_guid None and fill ambiguous_tools.
    """
    out: list[dict[str, Any]] = []
    for seg in segments:
        s = dict(seg)
        # Prefer explicit GUID
        guid = s.get("tool_guid")
        tool: dict[str, Any] | None = None
        if guid and get_tool is not None:
            tool = get_tool(str(guid))
        if tool is None and s.get("tool_number") is not None:
            matches = lookup_by_number(int(s["tool_number"]))
            if len(matches) == 1:
                tool = matches[0]
                s["tool_guid"] = tool.get("guid")
                s["ambiguous_tools"] = []
            elif len(matches) > 1:
                s["ambiguous_tools"] = [
                    {
                        "guid": m.get("guid"),
                        "description": m.get("description"),
                        "flutes": m.get("flutes"),
                        "diameter": m.get("diameter"),
                        "reference_rpm": m.get("reference_rpm"),
                    }
                    for m in matches
                ]
                if not s.get("tool_guid"):
                    s["tool_guid"] = None
            else:
                s["ambiguous_tools"] = []
        if tool:
            if s.get("flutes") is None and tool.get("flutes") is not None:
                s["flutes"] = tool.get("flutes")
            if s.get("diameter") is None and tool.get("diameter") is not None:
                s["diameter"] = tool.get("diameter")
            if s.get("diameter_mm") is None and tool.get("diameter_mm") is not None:
                s["diameter_mm"] = tool.get("diameter_mm")
            if not s.get("tool_name") and tool.get("description"):
                s["tool_name"] = tool.get("description")
            if s.get("reference_rpm") is None and tool.get("reference_rpm") is not None:
                s["reference_rpm"] = tool.get("reference_rpm")
                s["reference_rpm_source"] = (
                    tool.get("reference_rpm_source") or "fusion_reference"
                )
            # Stamp durable cutting presets at resolve time (library may change later)
            if s.get("feed_mm_min") is None and tool.get("feed_mm_min") is not None:
                s["feed_mm_min"] = tool.get("feed_mm_min")
            if s.get("chipload_mm") is None and tool.get("chipload_mm") is not None:
                s["chipload_mm"] = tool.get("chipload_mm")
            if (
                s.get("cutting_preset_name") is None
                and tool.get("cutting_preset_name") is not None
            ):
                s["cutting_preset_name"] = tool.get("cutting_preset_name")
        out.append(normalize_segment(s))
    return out


def enrich_segments_from_rrf_snapshot(
    segments: list[dict[str, Any]],
    tools_snapshot: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill tool_name / reference_rpm from RRF tools-snapshot when missing."""
    by_num: dict[int, dict[str, Any]] = {}
    for t in tools_snapshot or []:
        num = t.get("number")
        if num is None:
            continue
        try:
            by_num[int(num)] = t
        except (TypeError, ValueError):
            continue
    out: list[dict[str, Any]] = []
    for seg in segments:
        s = dict(seg)
        tn = s.get("tool_number")
        if tn is not None and int(tn) in by_num:
            snap = by_num[int(tn)]
            if not s.get("tool_name") and snap.get("name"):
                s["tool_name"] = snap["name"]
            rpm = snap.get("spindleRpm")
            if rpm is None:
                rpm = snap.get("spindle_rpm")
            if s.get("reference_rpm") is None and rpm is not None:
                try:
                    rpm_f = float(rpm)
                    if rpm_f > 0:
                        s["reference_rpm"] = rpm_f
                        s["reference_rpm_source"] = "rrf_spindle"
                except (TypeError, ValueError):
                    pass
        out.append(normalize_segment(s))
    return out
