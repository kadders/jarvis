"""Fuse gesture and audio channel scores into a cast decision."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FusionResult:
    spell_id: str | None
    score: float
    gesture_spell: str | None
    gesture_score: float
    audio_spell: str | None
    audio_score: float
    cast: bool
    reason: str


class FusionEngine:
    def __init__(self, alpha: float = 0.65, beta: float = 0.35, threshold: float = 0.55):
        self.alpha = alpha
        self.beta = beta
        self.threshold = threshold

    def fuse(
        self,
        gesture_probs: dict[str, float],
        audio_probs: dict[str, float],
        *,
        gesture_complete: bool,
    ) -> FusionResult:
        g_spell = max(gesture_probs, key=gesture_probs.get) if gesture_probs else None
        g_score = float(gesture_probs.get(g_spell, 0.0)) if g_spell else 0.0
        a_spell = max(audio_probs, key=audio_probs.get) if audio_probs else None
        a_score = float(audio_probs.get(a_spell, 0.0)) if a_spell else 0.0

        # Combine per-spell
        spell_ids = set(gesture_probs) | set(audio_probs)
        fused: dict[str, float] = {}
        for sid in spell_ids:
            fused[sid] = self.alpha * gesture_probs.get(sid, 0.0) + self.beta * audio_probs.get(
                sid, 0.0
            )

        best = max(fused, key=fused.get) if fused else None
        best_score = float(fused.get(best, 0.0)) if best else 0.0

        # Agreement bonus when both channels name the same spell
        if g_spell and a_spell and g_spell == a_spell:
            agree = min(1.0, self.alpha * g_score + self.beta * a_score + 0.2)
            if agree > best_score:
                best = g_spell
                best_score = agree

        # Park-like: gesture alone can cast when complete
        if not gesture_complete:
            return FusionResult(
                spell_id=best,
                score=best_score,
                gesture_spell=g_spell,
                gesture_score=g_score,
                audio_spell=a_spell,
                audio_score=a_score,
                cast=False,
                reason="gesture_incomplete",
            )

        # Strong audio that disagrees with gesture → withhold cast (avoid wrong FX)
        if a_spell and a_score >= 0.7 and g_spell and g_spell != a_spell:
            return FusionResult(
                spell_id=None,
                score=best_score,
                gesture_spell=g_spell,
                gesture_score=g_score,
                audio_spell=a_spell,
                audio_score=a_score,
                cast=False,
                reason="channel_conflict",
            )

        if best and best_score >= self.threshold:
            return FusionResult(
                spell_id=best,
                score=best_score,
                gesture_spell=g_spell,
                gesture_score=g_score,
                audio_spell=a_spell,
                audio_score=a_score,
                cast=True,
                reason="fused_threshold",
            )

        # Gesture-only cast when audio is absent or weak
        if g_spell and g_score >= self.threshold and a_score < 0.35:
            return FusionResult(
                spell_id=g_spell,
                score=g_score,
                gesture_spell=g_spell,
                gesture_score=g_score,
                audio_spell=a_spell,
                audio_score=a_score,
                cast=True,
                reason="gesture_only",
            )

        if a_spell and a_score >= 0.85 and g_spell == a_spell and g_score >= 0.25:
            return FusionResult(
                spell_id=a_spell,
                score=best_score,
                gesture_spell=g_spell,
                gesture_score=g_score,
                audio_spell=a_spell,
                audio_score=a_score,
                cast=True,
                reason="audio_gesture_agree",
            )

        return FusionResult(
            spell_id=best,
            score=best_score,
            gesture_spell=g_spell,
            gesture_score=g_score,
            audio_spell=a_spell,
            audio_score=a_score,
            cast=False,
            reason="below_threshold",
        )
