#!/usr/bin/env python3
"""Pi station client: track tip → POST /recognize; optional ASR transcript; drive effects."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from typing import Any

try:
    import cv2
except ImportError:
    cv2 = None

from effects import EffectController
from track_blob import BlobTracker, TipPoint, synthetic_frames


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    p = argparse.ArgumentParser(description="Jarvis wand Pi station client")
    p.add_argument("--wand-url", default="http://127.0.0.1:8002")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--spell", default="incendio", help="Synthetic path spell")
    p.add_argument("--transcript", default="", help="Optional spoken incantation text")
    p.add_argument("--hud", action="store_true")
    p.add_argument("--effects", action="store_true", help="Drive local LED/relay mocks")
    p.add_argument("--poll-casts", action="store_true")
    args = p.parse_args()

    tracker = BlobTracker()
    effects = EffectController(enabled=args.effects)
    points: list[dict[str, float]] = []

    if args.synthetic:
        frames = synthetic_frames(args.spell)
    else:
        if cv2 is None:
            print("opencv required", file=sys.stderr)
            sys.exit(1)
        cap = cv2.VideoCapture(args.camera)
        if not cap.isOpened():
            print("camera open failed; try --synthetic", file=sys.stderr)
            sys.exit(1)

        def _iter():
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                yield frame

        frames = _iter()

    for frame in frames:
        tip = tracker.process(frame)
        if tip:
            points.append({"t": tip.t, "x": tip.x, "y": tip.y, "confidence": tip.confidence})
        if args.hud and cv2 is not None:
            cv2.imshow("wand-station", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        # Recognize periodically once we have enough motion
        if len(points) >= 25 and len(points) % 10 == 0:
            body = {
                "points": points,
                "transcript": args.transcript,
                "gesture_complete": len(points) >= 35,
            }
            try:
                result = post_json(f"{args.wand_url}/api/v1/recognize", body)
                print(json.dumps(result))
                if result.get("cast"):
                    effects.play(result.get("spell_id") or "unknown", result.get("score", 0))
                    points = []
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"error": str(exc)}), file=sys.stderr)

    if args.poll_casts:
        last = get_json(f"{args.wand_url}/api/v1/effects/last")
        print(json.dumps({"last_cast": last}))

    effects.shutdown()


if __name__ == "__main__":
    main()
