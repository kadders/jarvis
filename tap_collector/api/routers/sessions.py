"""REST + WebSocket routes for tap sessions."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/sessions")
async def list_sessions(request: Request):
    handler = request.app.state.handler
    return {"sessions": handler.list_sessions()}


@router.post("/sessions/import")
async def import_session(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("live_spindle"),
    device_id: str = Form("import"),
):
    handler = request.app.state.handler
    raw = await file.read()
    if not raw:
        return JSONResponse({"error": "empty_file"}, status_code=400)
    try:
        result = await handler.import_upload(
            raw,
            filename=file.filename or "",
            mode=mode or "live_spindle",
            device_id=device_id or "import",
        )
    except ValueError as e:
        return JSONResponse({"error": "parse_failed", "detail": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": "import_failed", "detail": str(e)}, status_code=500)
    return result


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    handler = request.app.state.handler
    data = handler.get_session(session_id)
    if data is None:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    return data


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, request: Request):
    """Force-finalize a live session when MQTT end_session never arrives."""
    handler = request.app.state.handler
    try:
        return await handler.force_stop_session(session_id)
    except KeyError:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    request: Request,
    body: dict[str, Any] = Body(...),
):
    """Update allowlisted metadata: notes, import_label."""
    handler = request.app.state.handler
    kwargs: dict[str, Any] = {}
    if "notes" in body:
        kwargs["notes"] = body.get("notes")
    if "import_label" in body:
        kwargs["import_label"] = body.get("import_label")
    if not kwargs:
        return JSONResponse(
            {"error": "invalid_body", "detail": "notes and/or import_label required"},
            status_code=400,
        )
    try:
        return await handler.patch_session(session_id, **kwargs)
    except KeyError:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request, force: bool = False):
    """Delete session JSON/NPZ. Live sessions require force=true (finalize then delete)."""
    handler = request.app.state.handler
    try:
        return await handler.delete_session(session_id, force=force)
    except KeyError:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    except PermissionError:
        return JSONResponse(
            {
                "error": "session_recording",
                "detail": "Session is still recording; pass force=true to stop and delete",
                "session_id": session_id,
            },
            status_code=409,
        )


@router.get("/sessions/{session_id}/series")
async def session_series(
    session_id: str,
    request: Request,
    max_points: int = 2000,
    regenerate: bool = False,
):
    handler = request.app.state.handler
    data = handler.series_view(session_id, max_points=max_points, regenerate=regenerate)
    if data is None:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    return data


@router.get("/sessions/{session_id}/spectrum")
async def session_spectrum(
    session_id: str,
    request: Request,
    max_bins: int = 256,
    regenerate: bool = False,
):
    handler = request.app.state.handler
    data = handler.spectrum_view(session_id, max_bins=max_bins, regenerate=regenerate)
    if data is None:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    return data


@router.get("/sessions/{session_id}/motion")
async def session_motion(session_id: str, request: Request, max_points: int = 2000):
    handler = request.app.state.handler
    data = handler.motion_view(session_id, max_points=max_points)
    if data is None:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    return data


@router.post("/sessions/{session_id}/replay")
async def replay_session(
    session_id: str,
    request: Request,
    body: dict[str, Any] = Body(default={}),
):
    """Rebuild tools/motion (and optionally accel batches) from durable telemetry log."""
    handler = request.app.state.handler
    include_batches = bool(body.get("include_batches", False))
    restore_tools = bool(body.get("restore_tools", True))
    restore_motion = bool(body.get("restore_motion", True))
    reanalyze = bool(body.get("reanalyze", False))
    try:
        return await handler.replay_session_from_log(
            session_id,
            include_batches=include_batches,
            restore_tools=restore_tools,
            restore_motion=restore_motion,
            reanalyze=reanalyze,
        )
    except KeyError:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)


@router.get("/sessions/{session_id}/live")
async def session_live(session_id: str, request: Request):
    handler = request.app.state.handler
    data = handler.live_view(session_id)
    if data is None:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    return data


@router.get("/sessions/{session_id}/segments")
async def get_segments(session_id: str, request: Request):
    """Return segments + segment analytics (recomputes if stale/empty)."""
    handler = request.app.state.handler
    data = handler.get_segment_analysis(session_id)
    if data is None:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    return data


@router.put("/sessions/{session_id}/segments")
async def put_segments(
    session_id: str,
    request: Request,
    body: dict[str, Any] = Body(...),
):
    """
    Replace tool segments for a session and recompute analytics.

    Body: ``{ "segments": [ { t0, t1, tool_guid?, tool_number?, flutes?,
    reference_rpm?, reference_rpm_source?, note? } ], "reanalyze": true }``
    """
    handler = request.app.state.handler
    segments = body.get("segments")
    if segments is None:
        return JSONResponse(
            {"error": "invalid_body", "detail": "segments array required"},
            status_code=400,
        )
    if not isinstance(segments, list):
        return JSONResponse(
            {"error": "invalid_body", "detail": "segments must be a list"},
            status_code=400,
        )
    reanalyze = bool(body.get("reanalyze", True))
    # Resolve ambiguous / missing tools against library when guid provided
    lib = getattr(request.app.state, "tool_library", None)
    enriched: list[dict[str, Any]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            return JSONResponse(
                {"error": "invalid_body", "detail": "each segment must be an object"},
                status_code=400,
            )
        s = dict(seg)
        guid = s.get("tool_guid")
        if guid and lib is not None:
            tool = lib.get_tool(str(guid))
            if tool is None:
                return JSONResponse(
                    {"error": "unknown_tool", "guid": guid},
                    status_code=400,
                )
            if s.get("flutes") is None and tool.get("flutes") is not None:
                s["flutes"] = tool["flutes"]
            if s.get("tool_name") is None and tool.get("description"):
                s["tool_name"] = tool["description"]
            if s.get("reference_rpm") is None and tool.get("reference_rpm") is not None:
                s["reference_rpm"] = tool["reference_rpm"]
                s["reference_rpm_source"] = tool.get("reference_rpm_source") or "fusion_reference"
            if s.get("diameter") is None:
                s["diameter"] = tool.get("diameter")
            if s.get("diameter_mm") is None:
                s["diameter_mm"] = tool.get("diameter_mm")
            if s.get("tool_number") is None and tool.get("tool_number") is not None:
                s["tool_number"] = tool["tool_number"]
        # Manual RPM override is observed evidence
        if s.get("reference_rpm") is not None and not s.get("reference_rpm_source"):
            s["reference_rpm_source"] = "manual"
        s["manual"] = bool(s.get("manual", True))
        enriched.append(s)
    try:
        result = await handler.set_segments(session_id, enriched, reanalyze=reanalyze)
    except KeyError:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    except ValueError as e:
        return JSONResponse(
            {"error": "validation_failed", "detail": str(e)},
            status_code=400,
        )
    return result


@router.post("/sessions/{session_id}/segments/reanalyze")
async def reanalyze_segments(session_id: str, request: Request):
    """Re-run segment analytics with current segment assignments."""
    handler = request.app.state.handler
    data = handler.get_session(session_id)
    if data is None:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    try:
        result = await handler.set_segments(
            session_id,
            list(data.get("segments") or []),
            reanalyze=True,
        )
    except KeyError:
        return JSONResponse({"error": "not_found", "session_id": session_id}, status_code=404)
    except ValueError as e:
        return JSONResponse(
            {"error": "validation_failed", "detail": str(e)},
            status_code=400,
        )
    return result


@router.websocket("/sessions/{session_id}/ws")
async def session_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    handler = websocket.app.state.handler
    try:
        while True:
            data = handler.live_view(session_id)
            if data is None:
                await websocket.send_json({"error": "not_found", "session_id": session_id})
                break
            await websocket.send_json(data)
            # After a final stopped frame, keep the socket briefly then exit so
            # clients can reconnect without an infinite poll of a finished run.
            if data.get("final") or data.get("stopped") or data.get("recording_state") == "stopped":
                await asyncio.sleep(1.0)
                # Send one more heartbeat-style final frame then close
                final = handler.live_view(session_id) or data
                final["final"] = True
                await websocket.send_json(final)
                break
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
