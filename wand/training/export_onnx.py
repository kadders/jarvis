#!/usr/bin/env python3
"""Optional ONNX export stub — SVM stays pickle; document path for future CNN."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--model-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "cache" / "models" / "wand_gesture",
    )
    args = p.parse_args()
    note = {
        "status": "svm_pickle_primary",
        "gesture_svm": str(args.model_dir / "gesture_svm.pkl"),
        "onnx": None,
        "note": "Phase-1 uses sklearn SVM. Export ONNX when upgrading to 1D-CNN on gpu-rig.",
    }
    out = args.model_dir / "export_notes.json"
    args.model_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(note, indent=2), encoding="utf-8")
    print(json.dumps(note))


if __name__ == "__main__":
    main()
