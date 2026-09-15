"""Motion sample ingest helpers and API downsampling."""

from __future__ import annotations

from typing import Any


def motion_sample_from_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an MQTT motion payload into a stored motion sample."""
    t_s = data.get("t_s")
    if t_s is None:
        return None
    try:
        t_s_f = float(t_s)
    except (TypeError, ValueError):
        return None
    x, y, z = data.get("x"), data.get("y"), data.get("z")
    axes = data.get("axis_positions_mm")
    if x is None and isinstance(axes, dict):
        x, y, z = axes.get("X"), axes.get("Y"), axes.get("Z")
    if x is None or y is None or z is None:
        return None
    row: dict[str, Any] = {
        "t_s": t_s_f,
        "x": float(x),
        "y": float(y),
        "z": float(z),
        "source": str(data.get("source") or "unknown"),
    }
    a = data.get("a")
    if a is None and isinstance(axes, dict) and "A" in axes:
        a = axes.get("A")
    if a is not None:
        try:
            row["a"] = float(a)
        except (TypeError, ValueError):
            pass
    for key in (
        "feed_mm_min",
        "motion_mode",
        "file_position",
        "job_file",
        "rrf_status",
        "tool_number",
        "coord_frame",
    ):
        if data.get(key) is not None:
            row[key] = data[key]
    if isinstance(axes, dict) and axes:
        row["axis_positions_mm"] = axes
    return row


def downsample_motion(
    samples: list[dict[str, Any]], *, max_points: int = 2000
) -> list[dict[str, Any]]:
    if not samples or len(samples) <= max_points:
        return list(samples)
    step = max(1, len(samples) // max_points)
    return samples[::step][:max_points]


def motion_view_payload(
    session_id: str,
    samples: list[dict[str, Any]],
    *,
    max_points: int = 2000,
) -> dict[str, Any]:
    pts = downsample_motion(samples, max_points=max(2, max_points))
    has_a = any(p.get("a") is not None for p in samples)
    sources = sorted({str(p.get("source") or "unknown") for p in samples})
    bbox = None
    if pts:
        xs = [float(p["x"]) for p in pts if p.get("x") is not None]
        ys = [float(p["y"]) for p in pts if p.get("y") is not None]
        zs = [float(p["z"]) for p in pts if p.get("z") is not None]
        if xs and ys and zs:
            bbox = {
                "x_min": min(xs),
                "x_max": max(xs),
                "y_min": min(ys),
                "y_max": max(ys),
                "z_min": min(zs),
                "z_max": max(zs),
            }
    t_end = float(samples[-1]["t_s"]) if samples else None
    t_start = float(samples[0]["t_s"]) if samples else None
    return {
        "session_id": session_id,
        "n_samples": len(samples),
        "n_points": len(pts),
        "has_a": has_a,
        "sources": sources,
        "bbox": bbox,
        "t_start": t_start,
        "t_end": t_end,
        "points": pts,
    }
