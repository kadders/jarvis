from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from loguru import logger

from config_poller import ConfigPoller
from mqtt_bridge import MqttBridge
from supervisor import StreamSupervisor
from utils.config import Settings, get_settings

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    supervisor = StreamSupervisor(settings)
    bridge = MqttBridge(settings, supervisor)

    def _on_reload(new: Settings) -> None:
        app.state.settings = new
        bridge.settings = new

    poller = ConfigPoller(supervisor, on_reload=_on_reload)
    app.state.settings = settings
    app.state.supervisor = supervisor
    app.state.mqtt = bridge
    app.state.poller = poller
    await bridge.start()
    poller.start()
    logger.info(
        "tap-stream started on port {} device={} mjpeg={} youtube_enabled={} poll_s={}",
        settings.api_port,
        settings.device_id,
        settings.mjpeg_url,
        settings.youtube_enabled,
        settings.config_poll_s,
    )
    yield
    poller.stop()
    await bridge.stop()


app = FastAPI(title="Jarvis Tap Stream", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    supervisor: StreamSupervisor = app.state.supervisor
    bridge: MqttBridge = app.state.mqtt
    settings: Settings = supervisor.settings
    overlay_age = supervisor.overlay_age_s
    return {
        "status": "ok",
        "service": "tap_stream",
        "environment": settings.environment,
        "enabled": settings.enabled,
        "mqtt_connected": bridge.connected,
        "device_id": settings.device_id,
        "mjpeg_url": settings.mjpeg_url,
        "recordings_dir": str(settings.recordings_dir) if settings.recordings_dir else None,
        "output_mp4": supervisor.output_mp4,
        "youtube_enabled": settings.youtube_enabled,
        "youtube_configured": settings.youtube_configured,
        "ffmpeg_running": supervisor.running,
        "ffmpeg_restarts": supervisor.ffmpeg_restarts,
        "ffmpeg_parts": supervisor.part_count,
        "session_id": supervisor.session_id or None,
        "fps": settings.fps,
        "config_fingerprint": settings.config_fingerprint,
        "config_dir": str(settings.config_dir) if settings.config_dir else None,
        "config_poll_s": settings.config_poll_s,
        "overlay_age_s": overlay_age,
        "overlay_stale": (
            overlay_age is not None and overlay_age > settings.overlay_stale_s
        ),
    }


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("main:app", host=s.api_host, port=s.api_port, reload=False)
