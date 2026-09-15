"""Unit tests for wand lexicon, trajectory, fusion, recognize."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from models.fusion import FusionEngine
from models.gesture import GestureClassifier
from models.lexicon import LexiconMatcher
from utils.trajectory import feature_vector, rasterize_28, resample_polyline, stillness_completed


@pytest.fixture
def ontology_path() -> Path:
    return ROOT / "data" / "ontology" / "spells.json"


def test_lexicon_match(ontology_path: Path) -> None:
    lex = LexiconMatcher.from_path(ontology_path)
    m = lex.match("Expecto Patronum!")
    assert m is not None
    assert m.spell_id == "expecto_patronum"
    assert lex.match("expelliarmus please").spell_id == "expelliarmus"


def test_trajectory_features() -> None:
    pts = [(0.1, 0.1), (0.5, 0.9), (0.9, 0.1)]
    r = resample_polyline(pts, 16)
    assert r.shape == (16, 2)
    img = rasterize_28(pts)
    assert img.shape == (28, 28)
    assert feature_vector(pts).shape == (784,)
    assert stillness_completed([(0.5, 0.5)] * 8)


def test_gesture_heuristic() -> None:
    clf = GestureClassifier(Path("/tmp/no-model"), spell_ids=["incendio", "lumos"])
    pts = [(0.2, 0.2), (0.5, 0.85), (0.8, 0.2), (0.2, 0.2)]
    sid, score = clf.top(pts)
    assert sid == "incendio"
    assert score > 0.2
    assert clf.backend == "heuristic"


def test_fusion_gesture_only() -> None:
    f = FusionEngine(threshold=0.5)
    r = f.fuse({"incendio": 0.8}, {}, gesture_complete=True)
    assert r.cast
    assert r.spell_id == "incendio"


def test_fusion_conflict() -> None:
    f = FusionEngine(threshold=0.5)
    r = f.fuse({"wingardium_leviosa": 0.95}, {"incendio": 1.0}, gesture_complete=True)
    assert r.cast is False
    assert r.reason == "channel_conflict"


def test_recognize_api(ontology_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAND_ONTOLOGY_PATH", str(ontology_path))
    monkeypatch.setenv(
        "WAND_PARK_GESTURES_PATH",
        str(ROOT / "data" / "park" / "gestures.json"),
    )
    from utils.config import get_settings

    get_settings.cache_clear()
    # Re-import app after env so settings paths resolve
    import importlib

    import api.main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        assert client.get("/health").json()["service"] == "wand"
        body = {
            "points": [
                {"x": 0.2, "y": 0.2},
                {"x": 0.5, "y": 0.85},
                {"x": 0.8, "y": 0.2},
                {"x": 0.2, "y": 0.2},
            ],
            "transcript": "incendio",
            "gesture_complete": True,
        }
        resp = client.post("/api/v1/recognize", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["cast"] is True
        assert data["spell_id"] == "incendio"
        spells = client.get("/api/v1/spells").json()
        assert len(spells["spells"]) >= 6
