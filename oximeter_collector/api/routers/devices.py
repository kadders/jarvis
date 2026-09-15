"""Device and config routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class ConfigPushBody(BaseModel):
    v: int = 1
    wifi: dict[str, Any] | None = None
    mqtt: dict[str, Any] | None = None
    ble: dict[str, Any] | None = None
    sensor: dict[str, Any] | None = None
    via: str = Field(default="mqtt", pattern="^(mqtt|http|both)$")


@router.get("/config/template")
async def config_template(request: Request, device_id: str | None = None):
    handler = request.app.state.handler
    return handler.default_config_template(device_id)


@router.get("/devices/{device_id}/config")
async def fetch_device_config(device_id: str, request: Request):
    return await request.app.state.handler.fetch_config_http(device_id)


@router.get("/devices")
async def list_devices(request: Request):
    return {"devices": request.app.state.handler.list_devices()}


@router.get("/devices/{device_id}")
async def get_device(device_id: str, request: Request):
    rec = request.app.state.handler.get_device(device_id)
    if not rec:
        raise HTTPException(404, "device not found")
    out = rec.to_dict()
    out["open_session_id"] = request.app.state.handler.store._open_session.get(device_id)
    return out


@router.post("/devices/{device_id}/config")
async def push_config(device_id: str, body: ConfigPushBody, request: Request):
    handler = request.app.state.handler
    payload = body.model_dump(exclude={"via"}, exclude_none=True)
    results: dict[str, Any] = {}
    if body.via in ("mqtt", "both"):
        results["mqtt"] = handler.publish_config(device_id, payload)
    if body.via in ("http", "both"):
        results["http"] = await handler.push_config_http(device_id, payload)
    ok = all(r.get("ok") for r in results.values()) if results else False
    return {"ok": ok, "results": results}


@router.post("/devices/{device_id}/reboot")
async def reboot_device(device_id: str, request: Request):
    handler = request.app.state.handler
    if not handler._publisher:
        raise HTTPException(503, "mqtt not connected")
    topic = f"oximeter/{device_id}/cmd/reboot"
    handler._publisher(topic, {"cmd": "reboot"}, qos=0)
    return {"ok": True, "topic": topic}
