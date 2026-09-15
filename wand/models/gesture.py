"""Gesture classifier: SVM on 28x28 rasters with heuristic fallback."""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np

from utils.trajectory import feature_vector, rasterize_28

# Simple park-shape heuristics when no trained model is present.
# Coordinates are normalized [0,1]; shapes approximate medallion glyphs.
_HEURISTIC_TEMPLATES: dict[str, list[tuple[float, float]]] = {
    "incendio": [(0.2, 0.2), (0.5, 0.85), (0.8, 0.2), (0.2, 0.2)],  # triangle
    "alohomora": [
        (0.5, 0.85),
        (0.5, 0.15),
        (0.35, 0.45),
        (0.65, 0.45),
        (0.5, 0.15),
    ],  # phi-ish
    "lumos": [(0.5, 0.2), (0.5, 0.8)],  # vertical line
    "revelio": [(0.2, 0.3), (0.5, 0.8), (0.8, 0.3), (0.65, 0.2), (0.35, 0.2)],  # R-ish
    "arresto_momentum": [
        (0.2, 0.7),
        (0.35, 0.3),
        (0.5, 0.7),
        (0.65, 0.3),
        (0.8, 0.7),
    ],  # M
    "locomotor": [(0.3, 0.8), (0.3, 0.3), (0.55, 0.55), (0.7, 0.3), (0.7, 0.8)],  # 4-ish
    "wingardium_leviosa": [(0.2, 0.4), (0.5, 0.7), (0.8, 0.4), (0.65, 0.35)],  # swish
    "expelliarmus": [(0.2, 0.5), (0.8, 0.5), (0.55, 0.75)],  # slash
    "expecto_patronum": [(0.3, 0.2), (0.5, 0.85), (0.7, 0.2)],  # raise
}


def _template_score(points: list[tuple[float, float]], template: list[tuple[float, float]]) -> float:
    from utils.trajectory import resample_polyline

    a = resample_polyline(points, 32)
    b = resample_polyline(template, 32)
    dist = float(np.mean(np.linalg.norm(a - b, axis=1)))
    return float(max(0.0, 1.0 - dist * 1.4))


class GestureClassifier:
    def __init__(self, model_dir: Path, spell_ids: list[str] | None = None):
        self.model_dir = Path(model_dir)
        self.spell_ids = spell_ids or list(_HEURISTIC_TEMPLATES.keys())
        self._clf = None
        self._labels: list[str] = []
        self._load()

    def _load(self) -> None:
        pkl = self.model_dir / "gesture_svm.pkl"
        meta = self.model_dir / "gesture_meta.json"
        if pkl.exists() and meta.exists():
            with pkl.open("rb") as f:
                self._clf = pickle.load(f)
            meta_data = json.loads(meta.read_text(encoding="utf-8"))
            self._labels = list(meta_data.get("labels", []))

    @property
    def backend(self) -> str:
        return "svm" if self._clf is not None else "heuristic"

    def predict_proba(self, points: list[tuple[float, float]]) -> dict[str, float]:
        if len(points) < 2:
            return {sid: 0.0 for sid in self.spell_ids}

        if self._clf is not None and self._labels:
            vec = feature_vector(points).reshape(1, -1)
            if hasattr(self._clf, "predict_proba"):
                probs = self._clf.predict_proba(vec)[0]
                return {lab: float(p) for lab, p in zip(self._labels, probs)}
            pred = self._clf.predict(vec)[0]
            out = {lab: 0.0 for lab in self._labels}
            out[str(pred)] = 1.0
            return out

        scores = {
            sid: _template_score(points, tmpl)
            for sid, tmpl in _HEURISTIC_TEMPLATES.items()
            if sid in self.spell_ids or True
        }
        # keep only known spell ids if provided
        if self.spell_ids:
            scores = {k: v for k, v in scores.items() if k in self.spell_ids or k in _HEURISTIC_TEMPLATES}
        total = sum(scores.values()) or 1.0
        return {k: v / total for k, v in scores.items()}

    def top(self, points: list[tuple[float, float]]) -> tuple[str | None, float]:
        probs = self.predict_proba(points)
        if not probs:
            return None, 0.0
        sid = max(probs, key=probs.get)
        return sid, float(probs[sid])

    def raster(self, points: list[tuple[float, float]]) -> list[list[float]]:
        return rasterize_28(points).tolist()
