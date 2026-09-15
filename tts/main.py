"""
Minimal TTS HTTP API with optional Kubernetes ConfigMap hot-reload.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from k8s_config_reload import run_mounted_config_poll_loop


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    tts_enable_mock: bool = True
    jarvis_config_dir: Optional[str] = None
    jarvis_config_reload_interval_s: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _rebind_tts_runtime_state(app: FastAPI) -> None:
    app.state.settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = get_settings()
    app.state.config_reload_lock = asyncio.Lock()

    config_task: asyncio.Task | None = None
    if (get_settings().jarvis_config_dir or "").strip():
        config_task = asyncio.create_task(
            run_mounted_config_poll_loop(
                app,
                get_settings=get_settings,
                clear_settings_cache=get_settings.cache_clear,
                on_rebind=_rebind_tts_runtime_state,
                log_prefix="tts",
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


app = FastAPI(title="Jarvis TTS", version="0.1.0", lifespan=lifespan)


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice: Optional[str] = None
    language: Optional[str] = "en"


@app.get("/health")
async def health(request: Request):
    s = getattr(request.app.state, "settings", None) or get_settings()
    return {"status": "ok", "service": "tts", "mock": s.tts_enable_mock}


@app.post("/api/v1/synthesize")
async def synthesize(body: SynthesizeRequest, request: Request):
    s = getattr(request.app.state, "settings", None) or get_settings()
    if not s.tts_enable_mock:
        raise HTTPException(status_code=501, detail="Real TTS not configured")
    return PlainTextResponse(
        f"[mock-tts audio placeholder for: {body.text[:80]}...]",
        media_type="text/plain",
    )
