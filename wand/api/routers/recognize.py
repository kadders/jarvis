"""Recognition and live session API."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from models.fusion import FusionEngine
from utils.trajectory import stillness_completed

router = APIRouter(tags=["wand"])


class TrajectoryPoint(BaseModel):
    t: float | None = None
    x: float
    y: float
    confidence: float | None = 1.0


class RecognizeRequest(BaseModel):
    points: list[TrajectoryPoint]
    transcript: str | None = ""
    gesture_complete: bool | None = None
    session_id: str | None = None


class RecognizeResponse(BaseModel):
    spell_id: str | None
    score: float
    cast: bool
    reason: str
    gesture_spell: str | None
    gesture_score: float
    audio_spell: str | None
    audio_score: float
    gesture_backend: str
    gesture_probs: dict[str, float]
    audio_probs: dict[str, float]


class SpellCastEvent(BaseModel):
    event: str = "spell_cast"
    spell_id: str
    score: float
    session_id: str | None = None
    effects: list[str] = Field(default_factory=list)
    ts: float


def _points_xy(points: list[TrajectoryPoint]) -> list[tuple[float, float]]:
    return [(p.x, p.y) for p in points]


@router.post("/recognize", response_model=RecognizeResponse)
async def recognize(body: RecognizeRequest, request: Request) -> RecognizeResponse:
    handler = request.app.state.wand
    xy = _points_xy(body.points)
    complete = (
        body.gesture_complete
        if body.gesture_complete is not None
        else stillness_completed(xy)
    )
    g_probs = handler.gesture.predict_proba(xy)
    a_probs = handler.lexicon.probabilities(body.transcript or "")
    fusion: FusionEngine = handler.fusion
    result = fusion.fuse(g_probs, a_probs, gesture_complete=complete)

    if result.cast and result.spell_id:
        event = SpellCastEvent(
            spell_id=result.spell_id,
            score=result.score,
            session_id=body.session_id,
            effects=handler.effects_for(result.spell_id),
            ts=time.time(),
        )
        await handler.publish_cast(event.model_dump())

    return RecognizeResponse(
        spell_id=result.spell_id,
        score=result.score,
        cast=result.cast,
        reason=result.reason,
        gesture_spell=result.gesture_spell,
        gesture_score=result.gesture_score,
        audio_spell=result.audio_spell,
        audio_score=result.audio_score,
        gesture_backend=handler.gesture.backend,
        gesture_probs=g_probs,
        audio_probs=a_probs,
    )


@router.get("/spells")
async def list_spells(request: Request) -> dict[str, Any]:
    handler = request.app.state.wand
    return {
        "spells": list(handler.lexicon.spells.values()),
        "park_gestures": handler.park_gestures,
    }


@router.get("/catalog/manifest")
async def catalog_manifest(request: Request) -> dict[str, Any]:
    import yaml

    path = request.app.state.wand.settings.clips_manifest_path
    if not path.exists():
        return {"version": 1, "spells": []}
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@router.websocket("/sessions/live")
async def live_session(websocket: WebSocket) -> None:
    """Live trajectory stream: client sends JSON points; server replies with recognize."""
    await websocket.accept()
    handler = websocket.app.state.wand
    session_id = str(uuid.uuid4())
    buffer: list[TrajectoryPoint] = []
    transcript = ""
    await websocket.send_json({"type": "session_open", "session_id": session_id})
    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype == "point":
                buffer.append(TrajectoryPoint(**msg["point"]))
            elif mtype == "transcript":
                transcript = msg.get("text", "")
            elif mtype == "recognize":
                req = RecognizeRequest(
                    points=buffer,
                    transcript=transcript,
                    session_id=session_id,
                    gesture_complete=msg.get("gesture_complete"),
                )
                # reuse handler logic
                xy = _points_xy(req.points)
                complete = (
                    req.gesture_complete
                    if req.gesture_complete is not None
                    else stillness_completed(xy)
                )
                g_probs = handler.gesture.predict_proba(xy)
                a_probs = handler.lexicon.probabilities(req.transcript or "")
                result = handler.fusion.fuse(g_probs, a_probs, gesture_complete=complete)
                payload = {
                    "type": "recognize_result",
                    "spell_id": result.spell_id,
                    "score": result.score,
                    "cast": result.cast,
                    "reason": result.reason,
                    "gesture_backend": handler.gesture.backend,
                }
                if result.cast and result.spell_id:
                    event = {
                        "event": "spell_cast",
                        "spell_id": result.spell_id,
                        "score": result.score,
                        "session_id": session_id,
                        "effects": handler.effects_for(result.spell_id),
                        "ts": time.time(),
                    }
                    await handler.publish_cast(event)
                    payload["spell_cast"] = event
                    buffer = []
                await websocket.send_json(payload)
            elif mtype == "reset":
                buffer = []
                transcript = ""
                await websocket.send_json({"type": "reset_ack"})
            elif mtype == "close":
                await websocket.send_json({"type": "session_closed", "session_id": session_id})
                break
    except WebSocketDisconnect:
        pass
