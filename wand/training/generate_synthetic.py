#!/usr/bin/env python3
"""Generate synthetic gesture samples for SVM training."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from models.gesture import _HEURISTIC_TEMPLATES
from utils.trajectory import feature_vector


def jitter_path(path: list[tuple[float, float]], noise: float = 0.04) -> list[tuple[float, float]]:
    out = []
    for x, y in path:
        out.append(
            (
                float(np.clip(x + random.uniform(-noise, noise), 0, 1)),
                float(np.clip(y + random.uniform(-noise, noise), 0, 1)),
            )
        )
    dense = []
    for i in range(len(out) - 1):
        x0, y0 = out[i]
        x1, y1 = out[i + 1]
        steps = random.randint(4, 10)
        for k in range(steps):
            a = k / max(steps - 1, 1)
            dense.append((x0 + (x1 - x0) * a, y0 + (y1 - y0) * a))
    return dense or out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=ROOT / "data" / "training_samples.jsonl")
    p.add_argument("--per-spell", type=int, default=80)
    args = p.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for spell_id, tmpl in _HEURISTIC_TEMPLATES.items():
            for _ in range(args.per_spell):
                pts = jitter_path(tmpl)
                row = {
                    "spell_id": spell_id,
                    "points": pts,
                    "features": feature_vector(pts).tolist(),
                }
                f.write(json.dumps(row) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
