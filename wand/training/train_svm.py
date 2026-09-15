#!/usr/bin/env python3
"""Train sklearn SVM gesture classifier and save under cache/models/wand_gesture."""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import SVC

    p = argparse.ArgumentParser()
    p.add_argument(
        "--samples",
        type=Path,
        default=ROOT / "data" / "training_samples.jsonl",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT.parent / "cache" / "models" / "wand_gesture",
    )
    args = p.parse_args()

    if not args.samples.exists():
        from training.generate_synthetic import main as gen

        # regenerate default
        sys.argv = ["generate_synthetic.py", "--out", str(args.samples)]
        gen()

    X, y = [], []
    with args.samples.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            X.append(row["features"])
            y.append(row["spell_id"])
    X = np.asarray(X, dtype=np.float32)
    y = np.asarray(y)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    clf = make_pipeline(StandardScaler(), SVC(kernel="rbf", probability=True, C=4.0))
    clf.fit(Xtr, ytr)
    acc = float(clf.score(Xte, yte))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "gesture_svm.pkl").open("wb") as f:
        pickle.dump(clf, f)
    labels = sorted(set(y.tolist()))
    (args.out_dir / "gesture_meta.json").write_text(
        json.dumps({"labels": labels, "accuracy": acc, "backend": "sklearn_svm"}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"accuracy": acc, "out": str(args.out_dir), "n": len(y)}))


if __name__ == "__main__":
    main()
