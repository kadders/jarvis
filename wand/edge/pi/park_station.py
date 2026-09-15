#!/usr/bin/env python3
"""Phase 2–3 park-station loop: medallion prompt → recognize → effects."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

from effects import EffectController
from track_blob import BlobTracker, synthetic_frames

# Match models.gesture heuristic / training templates
TEMPLATES: dict[str, list[tuple[float, float]]] = {
    "incendio": [(0.2, 0.2), (0.5, 0.85), (0.8, 0.2), (0.2, 0.2)],
    "lumos": [(0.5, 0.2), (0.5, 0.8)],
    "alohomora": [(0.5, 0.85), (0.5, 0.15), (0.35, 0.45), (0.65, 0.45), (0.5, 0.15)],
    "revelio": [(0.2, 0.3), (0.5, 0.8), (0.8, 0.3), (0.65, 0.2), (0.35, 0.2)],
    "locomotor": [(0.3, 0.8), (0.3, 0.3), (0.55, 0.55), (0.7, 0.3), (0.7, 0.8)],
    "wingardium_leviosa": [(0.2, 0.4), (0.5, 0.7), (0.8, 0.4), (0.65, 0.35)],
}

SPELLS = [
    ("incendio", "Trace a triangle (Δ)"),
    ("lumos", "Draw a vertical line upward"),
    ("alohomora", "Trace a φ / unlock path"),
    ("revelio", "Draw an R shape"),
    ("locomotor", "Draw a 4-like path"),
    ("wingardium_leviosa", "Swish and flick"),
]


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def densify(path: list[tuple[float, float]], n: int = 40) -> list[dict]:
    pts: list[dict] = []
    for i in range(len(path) - 1):
        x0, y0 = path[i]
        x1, y1 = path[i + 1]
        steps = max(n // max(len(path) - 1, 1), 2)
        for k in range(steps):
            a = k / (steps - 1)
            pts.append({"x": x0 + (x1 - x0) * a, "y": y0 + (y1 - y0) * a, "confidence": 1.0})
    return pts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--wand-url", default="http://127.0.0.1:8002")
    p.add_argument(
        "--mode",
        choices=("templates", "blob"),
        default="templates",
        help="templates=ideal park paths (demo); blob=synthetic camera blob track",
    )
    p.add_argument(
        "--with-transcript",
        action="store_true",
        help="Include spoken spell (default park mode is gesture-only)",
    )
    args = p.parse_args()
    effects = EffectController(enabled=True)
    tracker = BlobTracker() if args.mode == "blob" else None

    for spell_id, prompt in SPELLS:
        print(json.dumps({"event": "medallion", "spell_id": spell_id, "prompt": prompt}))
        if args.mode == "templates":
            points = densify(TEMPLATES[spell_id])
        else:
            points = []
            assert tracker is not None
            for frame in synthetic_frames(spell_id if spell_id in TEMPLATES else "incendio"):
                tip = tracker.process(frame)
                if tip:
                    points.append(
                        {"x": tip.x, "y": tip.y, "t": tip.t, "confidence": tip.confidence}
                    )
                if len(points) >= 30:
                    break
        transcript = spell_id.replace("_", " ") if args.with_transcript else ""
        try:
            result = post_json(
                f"{args.wand_url}/api/v1/recognize",
                {
                    "points": points,
                    "transcript": transcript,
                    "gesture_complete": True,
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"event": "fail", "spell_id": spell_id, "error": str(exc)}))
            effects.play("fail", 0)
            continue
        print(
            json.dumps(
                {
                    "event": "result",
                    "spell_id": result.get("spell_id"),
                    "cast": result.get("cast"),
                    "reason": result.get("reason"),
                    "score": result.get("score"),
                }
            )
        )
        if result.get("cast"):
            effects.play(result.get("spell_id") or spell_id, result.get("score", 0))
        else:
            print(json.dumps({"event": "timeout_or_miss", "spell_id": spell_id}))
            effects.play("fail", 0)
        time.sleep(0.3)
    effects.shutdown()


if __name__ == "__main__":
    main()
