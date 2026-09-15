"""REST routes for the live MQTT event stream."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _parse_where(where: list[str] | None, where_csv: str | None) -> list[str]:
    out: list[str] = []
    if where:
        out.extend([w for w in where if w and str(w).strip()])
    if where_csv:
        for part in str(where_csv).split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


@router.get("/mqtt/events")
async def list_mqtt_events(
    request: Request,
    topic: str | None = Query(None, description="Substring match on MQTT topic"),
    q: str | None = Query(None, description="Free-text search over topic + payload"),
    where: list[str] | None = Query(
        None,
        description=(
            "JSON-key clause(s), e.g. event=change, tool_number=3, "
            "payload.session_id=s1, fn_hz>100. Repeat param or use where_csv."
        ),
    ),
    where_csv: str | None = Query(
        None,
        description="Comma-separated where clauses (alternative to repeated where=)",
    ),
    since_id: int | None = Query(None, description="Only events with id > since_id"),
    since_ts: float | None = Query(None, description="Only events with ts >= since_ts"),
    limit: int = Query(100, ge=1, le=1000),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """
    Query the in-memory MQTT event ring buffer.

    JSON-key filters use dotted paths into the message payload (and envelope
    fields ``topic``, ``id``, ``ts``, ``payload_kind``). Operators: ``=``,
    ``!=``, ``~`` (contains), ``>``, ``>=``, ``<``, ``<=``. A bare key means
    ``exists``.
    """
    handler = request.app.state.handler
    clauses = _parse_where(where, where_csv)
    return handler.mqtt_events.query(
        topic=topic,
        q=q,
        where=clauses,
        since_id=since_id,
        since_ts=since_ts,
        limit=limit,
        order=order,
    )


@router.get("/mqtt/events/summary")
async def mqtt_events_summary(request: Request):
    return request.app.state.handler.mqtt_events.summary()


@router.delete("/mqtt/events")
async def clear_mqtt_events(request: Request):
    handler = request.app.state.handler
    handler.mqtt_events.clear()
    return {"cleared": True, **handler.mqtt_events.summary()}
