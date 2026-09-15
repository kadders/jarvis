"""
Jarvis chat UI — FastAPI BFF for the LLM events API.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field

from config import Settings, get_settings
from k8s_config_reload import run_mounted_config_poll_loop

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _rebind_ui_runtime_state(app: FastAPI) -> None:
    app.state.settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = get_settings()
    app.state.config_reload_lock = asyncio.Lock()
    app.state.http = httpx.AsyncClient()

    config_task: asyncio.Task | None = None
    if (get_settings().jarvis_config_dir or "").strip():
        config_task = asyncio.create_task(
            run_mounted_config_poll_loop(
                app,
                get_settings=get_settings,
                clear_settings_cache=get_settings.cache_clear,
                on_rebind=_rebind_ui_runtime_state,
                log_prefix="ui",
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
        await app.state.http.aclose()


app = FastAPI(
    title="Jarvis Chat UI",
    version="0.1.0",
    lifespan=lifespan,
)


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    model_tier: Literal["default", "fast"] = "default"
    history: List[HistoryTurn] = Field(default_factory=list)


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _http(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "http", None)
    if client is None:
        client = httpx.AsyncClient()
        request.app.state.http = client
    return client


def _llm_root(settings: Settings) -> str:
    return settings.jarvis_llm_base_url.rstrip("/")


def _build_prompt(history: List[HistoryTurn], message: str, budget: int) -> str:
    """Flatten recent turns into a single text prompt for text_query."""
    lines: List[str] = []
    for turn in history:
        label = "User" if turn.role == "user" else "Assistant"
        lines.append(f"{label}: {turn.content.strip()}")
    lines.append(f"User: {message.strip()}")
    lines.append("Assistant:")
    text = "\n".join(lines)
    if len(text) <= budget:
        return text
    # Keep the tail (most recent context + new message).
    return text[-budget:]


@app.get("/api/health")
async def api_health(request: Request):
    settings = _settings(request)
    checked_at = datetime.now(timezone.utc).isoformat()
    llm_url = f"{_llm_root(settings)}/health"
    try:
        resp = await _http(request).get(
            llm_url,
            timeout=min(10.0, settings.jarvis_ui_llm_timeout_s),
        )
        if resp.status_code == 200:
            return {
                "ui": "ok",
                "llm": "ok",
                "checked_at": checked_at,
                "llm_base_url": _llm_root(settings),
            }
        return {
            "ui": "ok",
            "llm": "down",
            "detail": f"LLM /health HTTP {resp.status_code}",
            "checked_at": checked_at,
            "llm_base_url": _llm_root(settings),
        }
    except Exception as e:
        logger.warning(f"LLM health probe failed: {e}")
        return {
            "ui": "ok",
            "llm": "down",
            "detail": str(e),
            "checked_at": checked_at,
            "llm_base_url": _llm_root(settings),
        }


@app.post("/api/chat")
async def api_chat(body: ChatRequest, request: Request):
    settings = _settings(request)
    prompt = _build_prompt(
        body.history,
        body.message,
        settings.jarvis_ui_history_char_budget,
    )
    events_url = f"{_llm_root(settings)}/api/v1/events"
    idem_key = str(uuid.uuid4())
    payload: Dict[str, Any] = {
        "event_type": "text_query",
        "content": {"text": prompt},
        "metadata": {"model_tier": body.model_tier, "source": "jarvis_ui"},
    }
    try:
        resp = await _http(request).post(
            events_url,
            json=payload,
            headers={"Idempotency-Key": idem_key},
            timeout=settings.jarvis_ui_llm_timeout_s,
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="LLM API request timed out")
    except Exception as e:
        logger.error(f"LLM chat proxy error: {e}")
        raise HTTPException(status_code=502, detail=f"LLM API unreachable: {e}")

    if resp.status_code >= 400:
        detail: Any
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)

    data = resp.json()
    envelope = data.get("response") or {}
    result = envelope.get("result") or {}
    meta = envelope.get("meta") or {}
    reply = result.get("text")
    if not reply:
        raise HTTPException(
            status_code=502,
            detail="LLM API returned an empty result.text",
        )
    return {
        "reply": reply,
        "event_id": data.get("event_id"),
        "meta": {
            "tier": meta.get("tier"),
            "timing_ms": meta.get("timing_ms"),
            "backend": meta.get("backend"),
            "model": result.get("model"),
            "finish_reason": result.get("finish_reason"),
        },
    }


class FeedbackBody(BaseModel):
    event_id: str = Field(..., min_length=1)
    rating: int = Field(..., ge=-1, le=1)
    correction: Optional[str] = None


@app.post("/api/feedback")
async def api_feedback(body: FeedbackBody, request: Request):
    """Proxy thumbs / corrections to LLM API POST /api/v1/feedback."""
    settings = _settings(request)
    url = f"{_llm_root(settings)}/api/v1/feedback"
    payload: Dict[str, Any] = {
        "event_id": body.event_id,
        "rating": body.rating,
        "source": "ui",
    }
    if body.correction:
        payload["correction"] = body.correction
    try:
        resp = await _http(request).post(
            url,
            json=payload,
            timeout=min(30.0, settings.jarvis_ui_llm_timeout_s),
        )
    except Exception as e:
        logger.error(f"LLM feedback proxy error: {e}")
        raise HTTPException(status_code=502, detail=f"LLM API unreachable: {e}")
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


async def _proxy_feedback_get(request: Request, path: str) -> Any:
    """GET proxy to LLM /api/v1/feedback* with query string."""
    settings = _settings(request)
    url = f"{_llm_root(settings)}/api/v1/{path.lstrip('/')}"
    qs = request.url.query
    if qs:
        url = f"{url}?{qs}"
    try:
        resp = await _http(request).get(
            url,
            timeout=min(30.0, settings.jarvis_ui_llm_timeout_s),
        )
    except Exception as e:
        logger.error(f"LLM feedback GET proxy error: {e}")
        raise HTTPException(status_code=502, detail=f"LLM API unreachable: {e}")
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


@app.get("/api/feedback/summary")
async def api_feedback_summary(request: Request):
    return await _proxy_feedback_get(request, "feedback/summary")


@app.get("/api/feedback/list")
async def api_feedback_list(request: Request):
    """List feedback (avoids clashing with POST /api/feedback)."""
    return await _proxy_feedback_get(request, "feedback")


@app.get("/api/feedback/item/{feedback_id}")
async def api_feedback_item(feedback_id: str, request: Request):
    return await _proxy_feedback_get(request, f"feedback/{feedback_id}")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ui"}


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=500, detail="UI static assets missing")
    return FileResponse(index_path)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "main:app",
        host=s.jarvis_ui_host,
        port=s.jarvis_ui_port,
        reload=True,
    )
