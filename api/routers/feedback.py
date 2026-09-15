"""Feedback capture for preference learning / DPO."""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from models.llm_handler import LLMHandler
from utils.feedback_store import FeedbackStore, slice_tags_for
from utils.metrics import FEEDBACK_POST_TOTAL

router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    completion_id: Optional[str] = Field(
        None, description="OpenAI chat completion id (chatcmpl-...)"
    )
    event_id: Optional[str] = Field(None, description="Jarvis events API event_id")
    rating: int = Field(..., ge=-1, le=1, description="-1 bad, 0 neutral, +1 good")
    correction: Optional[str] = Field(
        None, description="Preferred / corrected assistant text"
    )
    preferred: Optional[str] = None
    rejected: Optional[str] = None
    prompt: Optional[str] = Field(
        None, description="Override prompt if completion/event not found"
    )
    response: Optional[str] = Field(
        None, description="Override response if completion/event not found"
    )
    model: Optional[str] = None
    source: Literal["ui", "cursor", "mcp", "api", "other"] = "api"
    metadata: Optional[Dict[str, Any]] = None


def get_llm_handler(request: Request) -> LLMHandler:
    return request.app.state.llm_handler


def get_feedback_store(request: Request) -> FeedbackStore:
    store = getattr(request.app.state, "feedback_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Feedback store not configured")
    return store


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    llm_handler: LLMHandler = Depends(get_llm_handler),
    store: FeedbackStore = Depends(get_feedback_store),
):
    if not body.completion_id and not body.event_id and not (body.prompt and body.response):
        raise HTTPException(
            status_code=400,
            detail="Provide completion_id, event_id, or both prompt and response",
        )

    prompt = body.prompt
    response = body.response
    model = body.model
    tier = None

    if body.completion_id:
        comp = llm_handler.lookup_completion(body.completion_id)
        if comp:
            prompt = prompt or comp.get("prompt")
            response = response or comp.get("response")
            model = model or comp.get("model")
            tier = comp.get("tier")
        elif not (prompt and response):
            FEEDBACK_POST_TOTAL.labels("not_found").inc()
            raise HTTPException(
                status_code=404,
                detail=f"Unknown completion_id: {body.completion_id}",
            )

    if body.event_id:
        ev = llm_handler.get_event_status(body.event_id)
        if ev:
            envelope = ev.get("response") or {}
            req = envelope.get("request") or {}
            result = envelope.get("result") or {}
            prompt = prompt or req.get("formatted_prompt") or str(req.get("content") or "")
            response = response or result.get("text")
            model = model or result.get("model")
            tier = (envelope.get("meta") or {}).get("tier")
        elif not (prompt and response) and not body.completion_id:
            FEEDBACK_POST_TOTAL.labels("not_found").inc()
            raise HTTPException(
                status_code=404,
                detail=f"Unknown event_id: {body.event_id}",
            )

    if not prompt or response is None:
        raise HTTPException(
            status_code=400,
            detail="Could not resolve prompt/response for feedback",
        )

    rejected = body.rejected
    preferred = body.preferred or body.correction
    if body.rating < 0 and body.correction and rejected is None:
        rejected = response

    record = {
        "completion_id": body.completion_id,
        "event_id": body.event_id,
        "rating": body.rating,
        "correction": body.correction,
        "preferred": preferred,
        "rejected": rejected,
        "prompt": prompt,
        "response": response,
        "model": model,
        "tier": tier,
        "source": body.source,
        "metadata": body.metadata or {},
    }
    try:
        saved = store.append(record)
    except Exception as e:
        FEEDBACK_POST_TOTAL.labels("error").inc()
        logger.error(f"feedback append failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    FEEDBACK_POST_TOTAL.labels("ok").inc()
    return {
        "status": "ok",
        "feedback_id": saved["feedback_id"],
        "created_at": saved["created_at"],
    }


@router.get("/feedback/stats")
async def feedback_stats(store: FeedbackStore = Depends(get_feedback_store)):
    return {"count": store.count(), "path": str(store.path)}


@router.get("/feedback/summary")
async def feedback_summary(
    store: FeedbackStore = Depends(get_feedback_store),
    day_limit: int = Query(30, ge=1, le=365),
):
    """Correctness aggregates by slice / source / day for the Learning UI."""
    return store.summary(day_limit=day_limit)


@router.get("/feedback")
async def list_feedback(
    store: FeedbackStore = Depends(get_feedback_store),
    rating: Optional[int] = Query(None, ge=-1, le=1),
    source: Optional[str] = Query(None),
    kind: Optional[str] = Query(
        None, pattern="^(outcome|other)$", description="outcome | other"
    ),
    slice: Optional[str] = Query(
        None,
        pattern="^(all|ask_local|agent_outcome|mcp|ui|python_jarvis|arduino_wand|rrf_nxt)$",
    ),
    domain: Optional[str] = Query(
        None,
        pattern="^(python_jarvis|arduino_wand|rrf_nxt|general)$",
    ),
    q: Optional[str] = Query(None, description="Substring search prompt/response"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """List feedback records (previews) for the Learning UI."""
    return store.list_records(
        rating=rating,
        source=source,
        kind=kind,
        slice_name=slice if slice and slice != "all" else None,
        domain=domain,
        q=q,
        limit=limit,
        offset=offset,
        order=order,
    )


@router.get("/feedback/{feedback_id}")
async def get_feedback(
    feedback_id: str,
    store: FeedbackStore = Depends(get_feedback_store),
):
    rec = store.get(feedback_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="not_found")
    out = dict(rec)
    out["slice_tags"] = slice_tags_for(rec)
    return out
