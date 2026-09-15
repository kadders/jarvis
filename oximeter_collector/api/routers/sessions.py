"""Session routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


@router.get("/sessions/{session_id}/analytics")
async def session_analytics(session_id: str, request: Request):
    data = request.app.state.handler.session_analytics(session_id)
    if not data:
        raise HTTPException(404, "session not found")
    return data


@router.get("/sessions/{session_id}/export.csv")
async def export_session_csv(session_id: str, request: Request):
    from fastapi.responses import PlainTextResponse

    series = request.app.state.handler.session_series(session_id, max_points=100000)
    if session_id not in request.app.state.handler.store.sessions:
        raise HTTPException(404, "session not found")
    lines = ["ts,hr,spo2,valid,finger,battery"]
    for p in series["points"]:
        lines.append(
            f"{p.get('ts','')},{p.get('hr','')},{p.get('spo2','')},"
            f"{p.get('valid','')},{p.get('finger','')},{p.get('battery','')}"
        )
    return PlainTextResponse("\n".join(lines), media_type="text/csv")


@router.get("/sessions")
async def list_sessions(
    request: Request,
    device_id: str | None = Query(None),
):
    return {"sessions": request.app.state.handler.list_sessions(device_id=device_id)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    data = request.app.state.handler.get_session(session_id)
    if not data:
        raise HTTPException(404, "session not found")
    return data


@router.get("/sessions/{session_id}/series")
async def session_series(
    session_id: str,
    request: Request,
    max_points: int = Query(2000, ge=10, le=20000),
):
    data = request.app.state.handler.session_series(session_id, max_points=max_points)
    if not data.get("points") and session_id not in request.app.state.handler.store.sessions:
        raise HTTPException(404, "session not found")
    return data


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, request: Request):
    handler = request.app.state.handler
    rec = handler.store.sessions.get(session_id)
    if not rec:
        raise HTTPException(404, "session not found")
    closed = handler.store.close_session(rec.device_id)
    if not closed:
        raise HTTPException(400, "session not open")
    return closed.to_dict()
