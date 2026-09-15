"""Wand service settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

_WAND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseModel):
    api_host: str = "0.0.0.0"
    api_port: int = 8002
    api_debug: bool = False
    environment: str = "development"
    ontology_path: Path = _WAND_ROOT / "data" / "ontology" / "spells.json"
    park_gestures_path: Path = _WAND_ROOT / "data" / "park" / "gestures.json"
    gesture_model_path: Path = Path(
        os.getenv("WAND_GESTURE_MODEL_PATH", str(_WAND_ROOT / ".." / "cache" / "models" / "wand_gesture"))
    )
    clips_manifest_path: Path = _WAND_ROOT / "data" / "clips" / "manifest.yaml"
    asr_base_url: str = "http://asr_service:8001"
    fusion_alpha: float = 0.65
    fusion_beta: float = 0.35
    cast_threshold: float = 0.55
    gesture_stillness_ms: int = 400


@lru_cache
def get_settings() -> Settings:
    root = _WAND_ROOT
    return Settings(
        api_host=os.getenv("WAND_API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("WAND_API_PORT", "8002")),
        api_debug=os.getenv("WAND_API_DEBUG", "false").lower() in ("true", "1", "t"),
        environment=os.getenv("ENVIRONMENT", "development"),
        ontology_path=Path(
            os.getenv("WAND_ONTOLOGY_PATH", str(root / "data" / "ontology" / "spells.json"))
        ),
        park_gestures_path=Path(
            os.getenv(
                "WAND_PARK_GESTURES_PATH",
                str(root / "data" / "park" / "gestures.json"),
            )
        ),
        gesture_model_path=Path(
            os.getenv(
                "WAND_GESTURE_MODEL_PATH",
                str(root.parent / "cache" / "models" / "wand_gesture"),
            )
        ),
        clips_manifest_path=Path(
            os.getenv(
                "WAND_CLIPS_MANIFEST_PATH",
                str(root / "data" / "clips" / "manifest.yaml"),
            )
        ),
        asr_base_url=os.getenv("WAND_ASR_BASE_URL", "http://asr_service:8001"),
        fusion_alpha=float(os.getenv("WAND_FUSION_ALPHA", "0.65")),
        fusion_beta=float(os.getenv("WAND_FUSION_BETA", "0.35")),
        cast_threshold=float(os.getenv("WAND_CAST_THRESHOLD", "0.55")),
        gesture_stillness_ms=int(os.getenv("WAND_GESTURE_STILLNESS_MS", "400")),
    )
