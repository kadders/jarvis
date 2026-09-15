"""Trajectory normalization helpers for wand tip paths."""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

Point = Sequence[float]
Trajectory = Sequence[Point]


def resample_polyline(points: Trajectory, n: int = 64) -> np.ndarray:
    """Resample an (x,y) polyline to ``n`` evenly spaced points in [0, 1]^2."""
    arr = np.asarray(points, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError("points must be Nx2")
    if len(arr) == 0:
        return np.zeros((n, 2), dtype=np.float64)
    if len(arr) == 1:
        p = arr[0, :2]
        return np.repeat(p.reshape(1, 2), n, axis=0)

    xy = arr[:, :2].copy()
    # Normalize to unit box
    mins = xy.min(axis=0)
    maxs = xy.max(axis=0)
    span = np.maximum(maxs - mins, 1e-6)
    xy = (xy - mins) / span

    deltas = np.diff(xy, axis=0)
    seg_len = np.linalg.norm(deltas, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = float(cum[-1])
    if total < 1e-9:
        return np.repeat(xy[:1], n, axis=0)

    targets = np.linspace(0.0, total, n)
    out = np.zeros((n, 2), dtype=np.float64)
    j = 0
    for i, t in enumerate(targets):
        while j < len(cum) - 2 and cum[j + 1] < t:
            j += 1
        seg = cum[j + 1] - cum[j]
        alpha = 0.0 if seg < 1e-9 else (t - cum[j]) / seg
        out[i] = xy[j] * (1 - alpha) + xy[j + 1] * alpha
    return out


def rasterize_28(points: Trajectory) -> np.ndarray:
    """Rasterize a trajectory into a 28x28 binary image (row-major flat optional)."""
    pts = resample_polyline(points, n=64)
    img = np.zeros((28, 28), dtype=np.float32)
    for x, y in pts:
        # y grows downward like image coords; flip so up is small row
        c = int(np.clip(round(x * 27), 0, 27))
        r = int(np.clip(round((1.0 - y) * 27), 0, 27))
        img[r, c] = 1.0
        # thicken slightly
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if 0 <= rr < 28 and 0 <= cc < 28:
                    img[rr, cc] = max(img[rr, cc], 0.5)
    return img


def feature_vector(points: Trajectory) -> np.ndarray:
    """784-d vector from 28x28 raster (SVM-friendly)."""
    return rasterize_28(points).reshape(-1)


def stillness_completed(
    points: Iterable[Point],
    *,
    window: int = 5,
    eps: float = 0.02,
) -> bool:
    """True when the last ``window`` points barely move (normalized coords)."""
    arr = list(points)
    if len(arr) < window + 2:
        return False
    recent = np.asarray(arr[-window:], dtype=np.float64)[:, :2]
    return float(np.linalg.norm(recent.max(axis=0) - recent.min(axis=0))) < eps
