"""Wand service runtime handler."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from models.fusion import FusionEngine
from models.gesture import GestureClassifier
from models.lexicon import LexiconMatcher
from utils.config import Settings


_SPELL_EFFECTS: dict[str, list[str]] = {
    "incendio": ["led_fire", "sfx_fire"],
    "alohomora": ["led_unlock", "relay_pulse", "sfx_click"],
    "lumos": ["led_white", "sfx_chime"],
    "revelio": ["led_reveal", "sfx_whoosh"],
    "arresto_momentum": ["led_blue", "sfx_stop"],
    "locomotor": ["led_green", "sfx_move"],
    "wingardium_leviosa": ["led_lift", "sfx_leviosa"],
    "expelliarmus": ["led_red", "sfx_disarm"],
    "expecto_patronum": ["led_silver", "sfx_patronus"],
}


class WandHandler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lexicon = LexiconMatcher.from_path(settings.ontology_path)
        spell_ids = list(self.lexicon.spells.keys())
        self.gesture = GestureClassifier(settings.gesture_model_path, spell_ids=spell_ids)
        self.fusion = FusionEngine(
            alpha=settings.fusion_alpha,
            beta=settings.fusion_beta,
            threshold=settings.cast_threshold,
        )
        self.park_gestures = self._load_json(settings.park_gestures_path)
        self.last_cast: dict[str, Any] | None = None
        self.last_ack: dict[str, Any] | None = None
        self._subscribers: list[asyncio.Queue] = []
        logger.info(
            "WandHandler ready ontology={} spells={} gesture_backend={}",
            settings.ontology_path,
            len(spell_ids),
            self.gesture.backend,
        )

    @staticmethod
    def _load_json(path: Path) -> Any:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def effects_for(self, spell_id: str) -> list[str]:
        return list(_SPELL_EFFECTS.get(spell_id, ["led_default", "sfx_default"]))

    async def publish_cast(self, event: dict[str, Any]) -> None:
        self.last_cast = event
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subscribers.append(q)
        return q
