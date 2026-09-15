from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect
from typing import List, Dict, Any, Optional
import asyncio
import json
import os
from pydantic import BaseModel
from loguru import logger

from api.deps import get_asr_handler
from models.asr_handler import ASRHandler
from utils.config import get_settings
from utils.audio_monitor import start_monitoring, stop_monitoring, get_monitor_status

router = APIRouter(tags=["audio_monitor"])

class MonitorStatus(BaseModel):
    """Status of the audio directory monitor"""
    active: bool
    directory: str
    files_processed: int
    last_processed_file: Optional[str] = None
    polling_interval: int

class AudioSource(BaseModel):
    """Audio source configuration"""
    name: str
    type: str
    path: Optional[str] = None
    status: str
    active: bool

@router.get("/monitor/status", response_model=MonitorStatus)
async def monitor_status():
    """Get the status of the audio directory monitor"""
    settings = get_settings()
    status = get_monitor_status()
    
    return MonitorStatus(
        active=status["active"],
        directory=settings.audio_input_dir,
        files_processed=status["files_processed"],
        last_processed_file=status["last_processed_file"],
        polling_interval=settings.polling_interval
    )

@router.post("/monitor/start")
async def start_monitor(
    asr_handler: ASRHandler = Depends(get_asr_handler)
):
    """Start the audio directory monitor if not already running"""
    settings = get_settings()
    status = get_monitor_status()
    
    if status["active"]:
        return {"message": "Monitor already running", "status": status}
    
    await start_monitoring(settings.audio_input_dir, asr_handler)
    return {"message": "Monitor started", "status": get_monitor_status()}

@router.post("/monitor/stop")
async def stop_monitor():
    """Stop the audio directory monitor if running"""
    status = get_monitor_status()
    
    if not status["active"]:
        return {"message": "Monitor not running", "status": status}
    
    await stop_monitoring()
    return {"message": "Monitor stopped", "status": get_monitor_status()}

@router.get("/sources", response_model=List[AudioSource])
async def list_audio_sources():
    """List available audio sources"""
    settings = get_settings()
    sources = []
    
    # Directory source
    sources.append(
        AudioSource(
            name="Directory Monitor",
            type="directory",
            path=settings.audio_input_dir,
            status="active" if get_monitor_status()["active"] else "inactive",
            active=get_monitor_status()["active"]
        )
    )
    
    # Add microphone source if available
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        info = p.get_host_api_info_by_index(0)
        num_devices = info.get('deviceCount')
        
        # Check if any input devices are available
        has_input = False
        for i in range(num_devices):
            if p.get_device_info_by_host_api_device_index(0, i).get('maxInputChannels') > 0:
                has_input = True
                break
        
        p.terminate()
        
        if has_input:
            sources.append(
                AudioSource(
                    name="Microphone",
                    type="microphone",
                    status="available",
                    active=False
                )
            )
    except:
        # PyAudio might not be available
        pass
    
    return sources

# WebSocket endpoint for live microphone streaming (stream_open / stream_close protocol; see docs)
@router.websocket("/stream")
async def websocket_endpoint(
    websocket: WebSocket,
    asr_handler: ASRHandler = Depends(get_asr_handler),
):
    await websocket.accept()
    settings = get_settings()
    legacy = settings.asr_stream_legacy_bytes
    active_session: Optional[str] = None
    await websocket.send_json(
        {
            "status": "connected",
            "message": "Ready for audio streaming",
            "legacy_bytes_allowed": legacy,
        }
    )
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "text" in message:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"status": "error", "message": "invalid json"}
                    )
                    continue
                msg_type = payload.get("type")
                if msg_type == "stream_open":
                    active_session = await asr_handler.stream_session_open(
                        payload.get("session_id")
                    )
                    await websocket.send_json(
                        {"type": "stream_ack", "session_id": active_session}
                    )
                elif msg_type == "stream_close":
                    if active_session:
                        sid = active_session
                        await asr_handler.stream_session_close(sid)
                        await websocket.send_json(
                            {"type": "stream_closed", "session_id": sid}
                        )
                        active_session = None
                    else:
                        await websocket.send_json(
                            {"status": "error", "message": "no active session"}
                        )
                else:
                    await websocket.send_json(
                        {
                            "status": "error",
                            "message": f"unknown message type: {msg_type}",
                        }
                    )
            elif "bytes" in message:
                if active_session is None and not legacy:
                    await websocket.send_json(
                        {
                            "status": "error",
                            "message": "send stream_open first (or set ASR_STREAM_LEGACY_BYTES=true)",
                        }
                    )
                    continue
                b = message["bytes"]
                if active_session:
                    result = await asr_handler.process_audio_chunk_scoped(
                        active_session, b
                    )
                    result = {**result, "session_id": active_session}
                else:
                    result = await asr_handler.process_audio_chunk(b)
                await websocket.send_json(result)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"Error in WebSocket: {str(e)}")
        try:
            await websocket.send_json({"status": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if active_session:
            await asr_handler.stream_session_close(active_session) 