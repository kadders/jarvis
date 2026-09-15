from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.routers import audio_monitor, transcription
from models.asr_handler import ASRHandler
from utils.audio_monitor import start_monitoring, stop_monitoring
from utils.config import get_settings
from utils.k8s_config_reload import run_mounted_config_poll_loop

load_dotenv()


async def _rebind_asr_runtime_state(app: FastAPI) -> None:
    """Recreate ASR handler (and optional directory monitor) after env/config change."""
    await stop_monitoring()
    settings = get_settings()
    app.state.asr_handler = ASRHandler(
        model_path=settings.asr_model_path,
        model_name=settings.asr_model_name,
    )
    from utils.audio_monitor import bind_asr_handler

    bind_asr_handler(app.state.asr_handler)
    if settings.monitor_audio_dir:
        await start_monitoring(settings.audio_input_dir, app.state.asr_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    os.makedirs(settings.audio_input_dir, exist_ok=True)
    os.makedirs(settings.audio_output_dir, exist_ok=True)

    app.state.asr_handler = ASRHandler(
        model_path=settings.asr_model_path,
        model_name=settings.asr_model_name,
    )
    from utils.audio_monitor import bind_asr_handler

    bind_asr_handler(app.state.asr_handler)
    app.state.config_reload_lock = asyncio.Lock()

    if settings.monitor_audio_dir:
        logger.info(f"Starting audio directory monitoring for {settings.audio_input_dir}")
        await start_monitoring(settings.audio_input_dir, app.state.asr_handler)

    config_task: asyncio.Task | None = None
    if (settings.jarvis_config_dir or "").strip():
        config_task = asyncio.create_task(
            run_mounted_config_poll_loop(
                app,
                get_settings=get_settings,
                clear_settings_cache=get_settings.cache_clear,
                on_rebind=_rebind_asr_runtime_state,
                log_prefix="asr",
                logging_env_keys=frozenset(),
                configure_logging_fn=None,
            )
        )

    try:
        yield
    finally:
        if config_task:
            config_task.cancel()
            try:
                await config_task
            except asyncio.CancelledError:
                pass
        await stop_monitoring()


app = FastAPI(
    title="Jarvis ASR Service",
    description="Automatic Speech Recognition service for the Jarvis stack",
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

app.include_router(transcription.router, prefix="/api/v1")
app.include_router(audio_monitor.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Jarvis ASR Service is running"}


@app.get("/health")
async def health_check(request: Request):
    h = getattr(request.app.state, "asr_handler", None)
    return {
        "status": "ok",
        "service": "asr",
        "version": "0.1.0",
        "models_loaded": h is not None,
        "environment": os.getenv("ENVIRONMENT", "development"),
    }


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_debug,
    )
