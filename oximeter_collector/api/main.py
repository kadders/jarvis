from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from api.routers import devices, mqtt_events, sessions, ws
from models.handler import OximeterCollectorHandler
from mqtt.subscriber import MqttBridge
from utils.config import get_settings

load_dotenv()

_STATIC = Path(__file__).resolve().parents[1] / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    handler = OximeterCollectorHandler(settings)
    bridge = MqttBridge(settings, handler)
    app.state.handler = handler
    app.state.mqtt = bridge
    await bridge.start()
    logger.info("Oximeter collector started on port {}", settings.api_port)
    yield
    await bridge.stop()


app = FastAPI(
    title="Jarvis Oximeter Collector",
    description="MQTT ingest, sessions, and config push for ESP32 oximeters",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(devices.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")
app.include_router(mqtt_events.router, prefix="/api/v1")
app.include_router(ws.router, prefix="/api/v1")


@app.get("/")
async def root():
    if _STATIC.is_dir():
        return RedirectResponse(url="/ui/")
    return {"status": "ok", "message": "Jarvis Oximeter Collector is running"}


@app.get("/ui")
async def ui_redirect():
    return RedirectResponse(url="/ui/")


@app.get("/health")
async def health():
    settings = get_settings()
    handler = getattr(app.state, "handler", None)
    return {
        "status": "ok",
        "service": "oximeter_collector",
        "version": "0.1.0",
        "environment": settings.environment,
        "mqtt_host": settings.mqtt_host,
        "devices": len(handler.store.devices) if handler else 0,
        "sessions": len(handler.store.sessions) if handler else 0,
        "mqtt_events": handler.mqtt_events.summary().get("n_events", 0) if handler else 0,
    }


if _STATIC.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_STATIC), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_debug,
    )
