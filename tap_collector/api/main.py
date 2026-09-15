from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from api.routers import mqtt_events, sessions, tools
from models.handler import TapCollectorHandler
from models.tool_library import ToolLibraryStore
from mqtt.subscriber import MqttBridge
from utils.config import get_settings

load_dotenv()

_STATIC = Path(__file__).resolve().parents[1] / "static"
_APP_VERSION = "0.5.1"


class NoCacheUiAssetsMiddleware(BaseHTTPMiddleware):
    """Prevent stale SPA JS after redeploys (browsers were keeping Dec-1969 uPlot axes)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/ui") and (
            path.endswith(".js")
            or path.endswith(".css")
            or path.endswith(".html")
            or path.endswith("/ui")
            or path.endswith("/ui/")
        ):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    handler = TapCollectorHandler(settings)
    tool_library = ToolLibraryStore(settings.session_store_dir)
    handler.set_tool_library(
        lookup_by_number=tool_library.lookup_by_number,
        get_tool=tool_library.get_tool,
    )
    bridge = MqttBridge(settings, handler)
    app.state.handler = handler
    app.state.tool_library = tool_library
    app.state.mqtt = bridge
    recovered = handler.recover_open_sessions_from_logs()
    await bridge.start()
    logger.info(
        "Tap collector started on port {} (tools={}, recovered_sessions={})",
        settings.api_port,
        tool_library.summary().get("n_tools", 0),
        recovered,
    )
    yield
    await bridge.stop()


app = FastAPI(
    title="Jarvis Tap Collector",
    description="MQTT ingest + CSV import + Fusion tool library + live/session analysis",
    version=_APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(NoCacheUiAssetsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router, prefix="/api/v1")
app.include_router(tools.router, prefix="/api/v1")
app.include_router(mqtt_events.router, prefix="/api/v1")


@app.get("/")
async def root():
    if _STATIC.is_dir():
        return RedirectResponse(url="/ui/")
    return {"status": "ok", "message": "Jarvis Tap Collector is running"}


@app.get("/ui")
async def ui_redirect():
    return RedirectResponse(url="/ui/")


@app.get("/health")
async def health():
    settings = get_settings()
    handler = getattr(app.state, "handler", None)
    lib = getattr(app.state, "tool_library", None)
    # Keep this cheap — probes time out if mqtt_events.summary() contends on NFS.
    mqtt_n = handler.mqtt_events.n_events_memory() if handler is not None else 0
    return {
        "status": "ok",
        "service": "tap_collector",
        "version": _APP_VERSION,
        "environment": settings.environment,
        "mqtt_host": settings.mqtt_host,
        "sessions": len(handler.sessions) if handler else 0,
        "tools": lib.summary().get("n_tools", 0) if lib else 0,
        "mqtt_events": mqtt_n,
        "llm_forward": settings.llm_forward_enabled,
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
