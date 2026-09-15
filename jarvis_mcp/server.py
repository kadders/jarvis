"""
Jarvis MCP server. Tools call the LLM API, optional ASR, and OCR HTTP APIs.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from typing import Any, Dict, Optional

import httpx
from loguru import logger
from mcp.server.fastmcp import FastMCP

DEFAULT_LLM = "http://127.0.0.1:8000/api/v1"
_mcp_host = os.environ.get("JARVIS_MCP_HOST", "0.0.0.0")
_mcp_port = int(os.environ.get("JARVIS_MCP_PORT", "8765"))
_mcp_path = os.environ.get("JARVIS_MCP_PATH", "/mcp")

mcp = FastMCP(
    "jarvis",
    host=_mcp_host,
    port=_mcp_port,
    streamable_http_path=_mcp_path,
)


def _llm_base() -> str:
    return os.environ.get("JARVIS_LLM_BASE_URL", DEFAULT_LLM).rstrip("/")


def _ocr_api_base() -> str:
    return os.environ.get("JARVIS_OCR_API_BASE_URL", _llm_base()).rstrip("/")


def _asr_base() -> str:
    return os.environ.get("JARVIS_ASR_BASE_URL", "").rstrip("/")


def _timeout() -> float:
    return float(os.environ.get("JARVIS_LLM_TIMEOUT_S", "120"))


def _max_retries() -> int:
    return int(os.environ.get("JARVIS_LLM_MAX_RETRIES", "3"))


def _http_error_json(r: httpx.Response) -> str:
    """Structured tool error for MCP hosts (avoid bare raise_for_status)."""
    rid = r.headers.get("X-Request-ID") or r.headers.get("x-request-id")
    preview = (r.text or "")[:2000]
    payload: Dict[str, Any] = {
        "error": "http_error",
        "status_code": r.status_code,
        "method": r.request.method,
        "url": str(r.request.url),
        "body_preview": preview,
        "request_id": rid,
    }
    ct = r.headers.get("content-type", "")
    if "json" in ct.lower():
        try:
            payload["response_json"] = r.json()
        except Exception:
            pass
    logger.warning(
        f"MCP HTTP tool error status={r.status_code} url={r.request.url!r} request_id={rid!r}"
    )
    return json.dumps(payload, ensure_ascii=False)


async def _request_with_retries(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    headers: Optional[Dict[str, str]] = None,
) -> httpx.Response:
    delay = 0.5
    last_err: Optional[Exception] = None
    for attempt in range(_max_retries() + 1):
        try:
            async with httpx.AsyncClient(timeout=_timeout()) as client:
                r = await client.request(
                    method, url, json=json_body, headers=headers
                )
            if r.status_code in (429, 502, 503, 504) and attempt < _max_retries():
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            return r
        except httpx.RequestError as e:
            last_err = e
            if attempt < _max_retries():
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            raise
    assert last_err
    raise last_err


@mcp.tool()
async def jarvis_submit_event(
    event_type: str,
    content: Dict[str, Any],
    idempotency_key: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    POST /events on the local Jarvis LLM (Ollama). Always prefer_local so cloud
    fallback is skipped; prior MCP/UI corrections are injected as few-shot learning.
    Returns full JSON (includes event_id and meta.learning_applied).
    """
    key = idempotency_key or str(uuid.uuid4())
    meta: Dict[str, Any] = dict(metadata or {})
    meta.setdefault("source", "mcp")
    meta["prefer_local"] = True
    body: Dict[str, Any] = {
        "event_type": event_type,
        "content": content,
        "metadata": meta,
    }
    r = await _request_with_retries(
        "POST",
        f"{_llm_base()}/events",
        json_body=body,
        headers={"Idempotency-Key": key},
    )
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


@mcp.tool()
async def jarvis_ask_local(
    text: str,
    model_tier: str = "default",
    idempotency_key: Optional[str] = None,
) -> str:
    """
    Ask the local Jarvis model (text_query). Uses Ollama only (prefer_local) and
    applies stored reinforcement corrections. Returns JSON with event_id, reply,
    and learning_applied. Use jarvis_submit_feedback on the event_id to teach.
    """
    tier = (model_tier or "default").strip().lower()
    if tier not in ("default", "fast"):
        tier = "default"
    return await jarvis_submit_event(
        event_type="text_query",
        content={"text": text},
        idempotency_key=idempotency_key,
        metadata={"model_tier": tier, "source": "mcp", "prefer_local": True},
    )


@mcp.tool()
async def jarvis_get_event_status(event_id: str) -> str:
    """GET /events/{event_id} from the LLM API."""
    r = await _request_with_retries("GET", f"{_llm_base()}/events/{event_id}")
    if r.status_code == 404:
        return json.dumps({"error": "not_found", "event_id": event_id})
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


@mcp.tool()
async def jarvis_submit_feedback(
    rating: int,
    event_id: Optional[str] = None,
    completion_id: Optional[str] = None,
    correction: Optional[str] = None,
    preferred: Optional[str] = None,
    rejected: Optional[str] = None,
    prompt: Optional[str] = None,
    response: Optional[str] = None,
    domain: Optional[str] = None,
) -> str:
    """
    Record reinforcement feedback for the local Jarvis model. Negative ratings
    with a correction are injected into later local completions (few-shot) and
    exported for DPO/QLoRA. Prefer passing event_id from jarvis_ask_local /
    jarvis_submit_event.
    """
    body: Dict[str, Any] = {"rating": rating, "source": "mcp"}
    meta: Dict[str, Any] = {}
    if domain and domain.strip():
        meta["domain"] = domain.strip()
    if meta:
        body["metadata"] = meta
    if event_id:
        body["event_id"] = event_id
    if completion_id:
        body["completion_id"] = completion_id
    if correction:
        body["correction"] = correction
    if preferred:
        body["preferred"] = preferred
    if rejected:
        body["rejected"] = rejected
    if prompt:
        body["prompt"] = prompt
    if response:
        body["response"] = response
    r = await _request_with_retries(
        "POST",
        f"{_llm_base()}/feedback",
        json_body=body,
    )
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


@mcp.tool()
async def jarvis_log_outcome(
    call: str,
    result: str,
    rating: int = 0,
    logic: Optional[str] = None,
    correction: Optional[str] = None,
    event_id: Optional[str] = None,
    completion_id: Optional[str] = None,
    domain: Optional[str] = None,
) -> str:
    """
    Log a call + end result for Jarvis training / trajectory follow-along.

    Use after jarvis_ask_local (or any agent task) so the local model store
    records what was asked, what happened, and optional brief logic. rating:
    1 = good outcome, -1 = bad (pass correction), 0 = observation only.
    """
    meta: Dict[str, Any] = {"kind": "outcome"}
    if logic and logic.strip():
        meta["logic"] = logic.strip()[:8000]
    if domain and domain.strip():
        meta["domain"] = domain.strip()
    body: Dict[str, Any] = {
        "rating": int(rating),
        "source": "mcp",
        "prompt": call,
        "response": result,
        "metadata": meta,
    }
    if event_id:
        body["event_id"] = event_id
    if completion_id:
        body["completion_id"] = completion_id
    if correction:
        body["correction"] = correction
    elif int(rating) > 0:
        # Successful trajectories become preferred text for DPO export.
        body["preferred"] = result
    r = await _request_with_retries(
        "POST",
        f"{_llm_base()}/feedback",
        json_body=body,
    )
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


@mcp.tool()
async def jarvis_feedback_stats() -> str:
    """GET /feedback/stats — how many preference records the local model has learned from."""
    r = await _request_with_retries("GET", f"{_llm_base()}/feedback/stats")
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


@mcp.tool()
async def jarvis_health() -> str:
    """GET /health on the LLM API root (not under /api/v1)."""
    base = _llm_base()
    root = base.rsplit("/api/v1", 1)[0] if "/api/v1" in base else base
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{root}/health")
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


@mcp.tool()
async def jarvis_monitor_start() -> str:
    """Start ASR directory monitor (requires JARVIS_ASR_BASE_URL)."""
    b = _asr_base()
    if not b:
        return json.dumps({"error": "JARVIS_ASR_BASE_URL not set"})
    r = await _request_with_retries("POST", f"{b}/monitor/start")
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


@mcp.tool()
async def jarvis_monitor_stop() -> str:
    """Stop ASR directory monitor."""
    b = _asr_base()
    if not b:
        return json.dumps({"error": "JARVIS_ASR_BASE_URL not set"})
    r = await _request_with_retries("POST", f"{b}/monitor/stop")
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


def _build_recognize_body(
    prompt: str,
    image_id: Optional[str],
    image_b64: Optional[str],
    lang: str,
    mode: str,
    response_delivery: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "prompt": prompt,
        "lang": lang,
        "mode": mode,
    }
    if image_id:
        body["image_id"] = image_id
    if image_b64:
        body["image_b64"] = image_b64
    if response_delivery:
        body["response_delivery"] = response_delivery
    return body


@mcp.tool()
async def jarvis_ocr_recognize(
    prompt: str,
    image_id: Optional[str] = None,
    image_b64: Optional[str] = None,
    lang: str = "eng",
    mode: str = "accurate",
    idempotency_key: Optional[str] = None,
    response_delivery: Optional[Dict[str, Any]] = None,
) -> str:
    """POST /api/v1/ocr/recognize — OCR + object detection (see docs/services/ocr.md)."""
    if bool(image_id) == bool(image_b64):
        return json.dumps(
            {
                "error": "invalid_arguments",
                "message": "Exactly one of image_id or image_b64 must be set",
            }
        )
    if len(prompt) > 4096:
        return json.dumps({"error": "prompt_too_long", "message": "prompt exceeds 4096 characters"})
    if mode not in ("fast", "accurate"):
        return json.dumps({"error": "invalid_arguments", "message": "mode must be fast or accurate"})

    key = idempotency_key or str(uuid.uuid4())
    body = _build_recognize_body(prompt, image_id, image_b64, lang, mode, response_delivery)
    r = await _request_with_retries(
        "POST",
        f"{_ocr_api_base()}/ocr/recognize",
        json_body=body,
        headers={"Idempotency-Key": key},
    )
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


@mcp.tool()
async def jarvis_ocr_health() -> str:
    """GET /api/v1/ocr/health as JSON text."""
    r = await _request_with_retries("GET", f"{_ocr_api_base()}/ocr/health")
    if r.status_code >= 400:
        return _http_error_json(r)
    return r.text


def main() -> None:
    transport = os.environ.get("JARVIS_MCP_TRANSPORT", "stdio").lower().strip()
    if transport == "both":
        app = mcp.streamable_http_app()

        def _run_http() -> None:
            import uvicorn

            uvicorn.run(
                app,
                host=mcp.settings.host,
                port=mcp.settings.port,
                log_level="warning",
                access_log=False,
            )

        threading.Thread(target=_run_http, daemon=True, name="jarvis-mcp-http").start()
        logger.info(
            f"MCP streamable-http on http://{mcp.settings.host}:{mcp.settings.port}"
            f"{mcp.settings.streamable_http_path} (stdio primary)"
        )
        mcp.run(transport="stdio")
    elif transport in ("streamable-http", "streamable_http"):
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
