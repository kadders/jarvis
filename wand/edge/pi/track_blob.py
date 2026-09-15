#!/usr/bin/env python3
"""IR tip blob tracker for Pi NoIR (Phase 0 optics).

Supports live camera, recorded video, or synthetic blob for bring-up without hardware.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore


@dataclass
class TipPoint:
    t: float
    x: float
    y: float
    confidence: float
    area: float = 0.0


class BlobTracker:
    def __init__(
        self,
        *,
        thresh: int = 200,
        min_area: float = 20.0,
        max_area: float = 5000.0,
        use_mog2: bool = True,
    ):
        if cv2 is None:
            raise RuntimeError("opencv-python is required for BlobTracker")
        self.thresh = thresh
        self.min_area = min_area
        self.max_area = max_area
        self.bg = cv2.createBackgroundSubtractorMOG2(history=120, varThreshold=16, detectShadows=False) if use_mog2 else None

    def process(self, frame_bgr: np.ndarray, t: float | None = None) -> TipPoint | None:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if self.bg is not None:
            mask = self.bg.apply(gray)
            _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        else:
            _, mask = cv2.threshold(gray, self.thresh, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h, w = gray.shape
        best = None
        best_area = 0.0
        for c in contours:
            area = float(cv2.contourArea(c))
            if area < self.min_area or area > self.max_area:
                continue
            if area > best_area:
                best_area = area
                best = c
        if best is None:
            return None
        m = cv2.moments(best)
        if m["m00"] == 0:
            return None
        cx = m["m10"] / m["m00"]
        cy = m["m01"] / m["m00"]
        return TipPoint(
            t=time.time() if t is None else t,
            x=float(cx / w),
            y=float(1.0 - cy / h),  # y-up normalized
            confidence=min(1.0, best_area / 200.0),
            area=best_area,
        )


def synthetic_frames(
    spell: str = "incendio",
    size: tuple[int, int] = (320, 240),
    n: int = 40,
) -> Iterator[np.ndarray]:
    """Generate bright-tip frames tracing a park-like shape (no camera required)."""
    if cv2 is None:
        raise RuntimeError("opencv-python required")
    w, h = size
    templates = {
        "incendio": [(0.2, 0.2), (0.5, 0.85), (0.8, 0.2), (0.2, 0.2)],
        "lumos": [(0.5, 0.2), (0.5, 0.8)],
        "alohomora": [(0.5, 0.85), (0.5, 0.15), (0.35, 0.45), (0.65, 0.45), (0.5, 0.15)],
        "locomotor": [(0.3, 0.8), (0.3, 0.3), (0.55, 0.55), (0.7, 0.3), (0.7, 0.8)],
    }
    path = templates.get(spell, templates["incendio"])
    # interpolate
    pts = []
    for i in range(len(path) - 1):
        x0, y0 = path[i]
        x1, y1 = path[i + 1]
        for k in range(n // max(len(path) - 1, 1)):
            a = k / max(n // max(len(path) - 1, 1) - 1, 1)
            pts.append((x0 + (x1 - x0) * a, y0 + (y1 - y0) * a))
    for x, y in pts:
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        px = int(x * (w - 1))
        py = int((1.0 - y) * (h - 1))
        cv2.circle(frame, (px, py), 6, (255, 255, 255), -1)
        yield frame


def run_optics_check(args: argparse.Namespace) -> int:
    tracker = BlobTracker(thresh=args.thresh, use_mog2=not args.no_mog2)
    points: list[TipPoint] = []
    show = args.hud and cv2 is not None

    if args.synthetic:
        frames = synthetic_frames(args.spell)
        source = "synthetic"
    elif args.video:
        cap = cv2.VideoCapture(args.video)
        if not cap.isOpened():
            print(f"Failed to open video {args.video}", file=sys.stderr)
            return 1

        def _iter():
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                yield frame

        frames = _iter()
        source = args.video
    else:
        cap = cv2.VideoCapture(args.camera)
        if not cap.isOpened():
            print(
                f"Failed to open camera index {args.camera}. "
                "Use --synthetic for bring-up without hardware.",
                file=sys.stderr,
            )
            return 1

        def _iter_cam():
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                yield frame

        frames = _iter_cam()
        source = f"camera:{args.camera}"

    print(json.dumps({"event": "optics_start", "source": source}))
    trail: list[tuple[int, int]] = []
    for frame in frames:
        tip = tracker.process(frame)
        if tip:
            points.append(tip)
            if show:
                h, w = frame.shape[:2]
                px, py = int(tip.x * w), int((1.0 - tip.y) * h)
                trail.append((px, py))
                for i in range(1, len(trail)):
                    cv2.line(frame, trail[i - 1], trail[i], (0, 255, 0), 2)
                cv2.circle(frame, (px, py), 8, (0, 0, 255), 2)
        if show:
            cv2.imshow("wand-optics", frame)
            if cv2.waitKey(30) & 0xFF == ord("q"):
                break
        if args.max_points and len(points) >= args.max_points:
            break

    if show:
        cv2.destroyAllWindows()

    lock_ratio = len(points) / max(args.max_points or len(points) or 1, 1)
    # For synthetic, expect nearly full lock
    ok = len(points) >= 10
    result = {
        "event": "optics_result",
        "source": source,
        "points": len(points),
        "lock_ok": ok,
        "sample": [asdict(p) for p in points[:5]],
    }
    print(json.dumps(result))
    if args.out:
        Path(args.out).write_text(
            "\n".join(json.dumps(asdict(p)) for p in points) + "\n",
            encoding="utf-8",
        )
    return 0 if ok else 2


def main() -> None:
    p = argparse.ArgumentParser(description="Phase 0 IR blob optics check")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--video", type=str, default="")
    p.add_argument("--synthetic", action="store_true", help="No camera; synthetic bright tip")
    p.add_argument("--spell", default="incendio")
    p.add_argument("--thresh", type=int, default=200)
    p.add_argument("--no-mog2", action="store_true")
    p.add_argument("--hud", action="store_true")
    p.add_argument("--max-points", type=int, default=60)
    p.add_argument("--out", type=str, default="")
    args = p.parse_args()
    if cv2 is None:
        print("Install opencv-python-headless or opencv-python", file=sys.stderr)
        sys.exit(1)
    sys.exit(run_optics_check(args))


if __name__ == "__main__":
    main()
