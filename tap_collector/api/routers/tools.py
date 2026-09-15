"""Fusion tool library REST routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse

router = APIRouter()


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "t", "yes", "on")


@router.get("/tools")
async def list_tools(request: Request, q: str | None = Query(None)):
    lib = request.app.state.tool_library
    return {"tools": lib.list_tools(q=q), "summary": lib.summary()}


@router.get("/tools/summary")
async def tools_summary(request: Request):
    return request.app.state.tool_library.summary()


@router.get("/tools/lookup")
async def lookup_tools(request: Request, number: int = Query(...)):
    lib = request.app.state.tool_library
    return {"number": number, "tools": lib.lookup_by_number(number)}


@router.get("/tools/{guid}")
async def get_tool(guid: str, request: Request):
    lib = request.app.state.tool_library
    tool = lib.get_tool(guid)
    if tool is None:
        return JSONResponse({"error": "not_found", "guid": guid}, status_code=404)
    return tool


@router.patch("/tools/{guid}")
async def patch_tool(
    guid: str,
    request: Request,
    body: dict[str, Any] = Body(...),
):
    lib = request.app.state.tool_library
    if not isinstance(body, dict) or not body:
        return JSONResponse(
            {"error": "invalid_body", "detail": "object with fields required"},
            status_code=400,
        )
    try:
        return lib.patch_tool(guid, body)
    except KeyError:
        return JSONResponse({"error": "not_found", "guid": guid}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": "validation_failed", "detail": str(e)}, status_code=400)


@router.delete("/tools/{guid}")
async def delete_tool(guid: str, request: Request):
    lib = request.app.state.tool_library
    try:
        return lib.delete_tool(guid)
    except KeyError:
        return JSONResponse({"error": "not_found", "guid": guid}, status_code=404)


@router.post("/tools/import")
async def import_tools(
    request: Request,
    file: UploadFile = File(...),
    replace: str | None = Form(None),
    replace_q: str | None = Query(None, alias="replace"),
):
    lib = request.app.state.tool_library
    raw = await file.read()
    if not raw:
        return JSONResponse({"error": "empty_file"}, status_code=400)
    do_replace = _parse_bool(replace if replace is not None else replace_q, False)
    try:
        result = lib.import_bytes(
            raw, filename=file.filename or "", replace=do_replace
        )
    except ValueError as e:
        return JSONResponse({"error": "parse_failed", "detail": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": "import_failed", "detail": str(e)}, status_code=500)
    return result
