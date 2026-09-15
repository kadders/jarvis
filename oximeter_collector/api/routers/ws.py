"""WebSocket live device feed."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/devices/{device_id}/ws")
async def device_ws(websocket: WebSocket, device_id: str):
    await websocket.accept()
    handler = websocket.app.state.handler
    queue = handler.subscribe_device(device_id)
    try:
        rec = handler.get_device(device_id)
        if rec:
            await websocket.send_json({"type": "device", "data": rec.to_dict()})
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                await websocket.send_json(msg)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        handler.unsubscribe_device(device_id, queue)
