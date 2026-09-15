"""Spell ontology / incantation lexicon matcher."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _norm(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class SpellMatch:
    spell_id: str
    score: float
    matched_phrase: str


class LexiconMatcher:
    def __init__(self, ontology: dict[str, Any]):
        self.spells: dict[str, dict[str, Any]] = {
            s["spell_id"]: s for s in ontology.get("spells", [])
        }
        self._phrases: list[tuple[str, str]] = []
        for sid, meta in self.spells.items():
            for phrase in meta.get("incantations", []):
                self._phrases.append((_norm(phrase), sid))
        # longest phrase first for greedy match
        self._phrases.sort(key=lambda x: len(x[0]), reverse=True)

    @classmethod
    def from_path(cls, path: Path) -> "LexiconMatcher":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data)

    def match(self, transcript: str) -> SpellMatch | None:
        text = _norm(transcript)
        if not text:
            return None
        best: SpellMatch | None = None
        for phrase, sid in self._phrases:
            if phrase and phrase in text:
                # score by coverage of transcript
                score = min(1.0, len(phrase) / max(len(text), 1) + 0.45)
                if best is None or score > best.score:
                    best = SpellMatch(spell_id=sid, score=score, matched_phrase=phrase)
        # fuzzy token overlap for noisy ASR
        if best is None:
            tokens = set(text.split())
            for phrase, sid in self._phrases:
                ptoks = set(phrase.split())
                if not ptoks:
                    continue
                overlap = len(tokens & ptoks) / len(ptoks)
                if overlap >= 0.6:
                    score = 0.4 + 0.5 * overlap
                    if best is None or score > best.score:
                        best = SpellMatch(spell_id=sid, score=score, matched_phrase=phrase)
        return best

    def probabilities(self, transcript: str) -> dict[str, float]:
        m = self.match(transcript)
        out = {sid: 0.0 for sid in self.spells}
        if m:
            out[m.spell_id] = m.score
        return out
