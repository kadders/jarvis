"""Effect preview / last cast events for edge clients."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["effects"])


class EffectAck(BaseModel):
    spell_id: str
    ok: bool = True
    detail: str = ""


@router.get("/effects/last")
async def last_cast(request: Request) -> dict:
    return request.app.state.wand.last_cast or {"event": None}


@router.post("/effects/ack")
async def ack_effect(body: EffectAck, request: Request) -> dict:
    request.app.state.wand.last_ack = body.model_dump()
    return {"status": "ok"}
