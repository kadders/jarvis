from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request, Header
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from loguru import logger

from models.llm_handler import LLMHandler
from utils.config import get_settings
from utils.idempotency import canonical_body_hash
from utils.metrics import EVENTS_POST_TOTAL, IDEMPOTENCY_REPLAY_TOTAL

router = APIRouter(tags=["events"])

# Event models
class EventData(BaseModel):
    """Model for incoming event data"""
    content: Dict[str, Any] = Field(..., description="The content of the event in JSON format")
    event_type: str = Field(..., description="Type of the event")
    source: Optional[str] = Field(None, description="Source of the event")
    timestamp: Optional[str] = Field(None, description="Timestamp of the event")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")

class EventResponse(BaseModel):
    """Model for event processing response"""
    event_id: str
    status: str
    response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def get_llm_handler(request: Request) -> LLMHandler:
    return request.app.state.llm_handler


@router.post("/events", response_model=EventResponse)
async def process_event(
    request: Request,
    event: EventData,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    llm_handler: LLMHandler = Depends(get_llm_handler),
):
    """
    Process an incoming event with the LLM

    This endpoint accepts JSON-formatted event data and processes it using
    the configured LLM model. It can handle various event types as specified
    in the event_type field.

    Optional header **Idempotency-Key**: same key and body replays the stored
    response; same key with a different body returns **409 Conflict**.
    """
    settings = get_settings()
    if settings.require_idempotency_key and not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required",
        )

    body = event.model_dump()
    body_hash = canonical_body_hash(body)
    store = request.app.state.idempotency_store

    if idempotency_key:
        existing = store.get(idempotency_key)
        if existing:
            if existing.body_hash != body_hash:
                EVENTS_POST_TOTAL.labels("idempotency_conflict").inc()
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was reused with a different request body",
                )
            IDEMPOTENCY_REPLAY_TOTAL.inc()
            EVENTS_POST_TOTAL.labels("idempotency_replay").inc()
            return EventResponse(
                event_id=existing.event_id,
                status=existing.status,
                response=existing.response,
                error=existing.error,
            )

    try:
        logger.info(
            f"Received event of type: {event.event_type} from source: {event.source}"
        )
        event_id, response = llm_handler.process_event(body)

        out = EventResponse(
            event_id=event_id,
            status="success",
            response=response,
        )
        if idempotency_key:
            store.set(
                idempotency_key,
                body_hash,
                event_id,
                out.status,
                out.response,
                out.error,
                settings.idempotency_ttl_seconds,
            )
        EVENTS_POST_TOTAL.labels("success").inc()
        return out

    except HTTPException:
        raise
    except Exception as e:
        EVENTS_POST_TOTAL.labels("exception").inc()
        logger.error(f"Error processing event: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error processing event: {str(e)}"
        )


@router.get("/events/{event_id}", response_model=EventResponse)
async def get_event_status(
    event_id: str,
    llm_handler: LLMHandler = Depends(get_llm_handler),
):
    """Get the status of a previously submitted event"""
    try:
        result = llm_handler.get_event_status(event_id)
        if not result:
            raise HTTPException(
                status_code=404, detail=f"Event with ID {event_id} not found"
            )

        return EventResponse(
            event_id=event_id,
            status=result.get("status", "unknown"),
            response=result.get("response"),
            error=result.get("error"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting event status: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error getting event status: {str(e)}"
        )
