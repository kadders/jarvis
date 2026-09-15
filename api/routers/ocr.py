from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from utils.config import Settings, get_settings
from utils.idempotency import canonical_body_hash
from utils.metrics import record_ocr_recognize
from utils.ocr.health_info import build_ocr_health
from utils.ocr.image_store import ImageStore
from utils.ocr.pipeline import run_recognition
from utils.ocr.relay import RelayError, deliver_webhook, validate_relay_target
from utils.ocr.uuid4 import is_uuid_v4

router = APIRouter(tags=["ocr"])


def _settings() -> Settings:
    return get_settings()


def _image_store(request: Request) -> ImageStore:
    return request.app.state.image_store


_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")

_MAX_PROMPT = 4096


class ResponseDelivery(BaseModel):
    url: str
    method: str = "POST"
    headers: Optional[Dict[str, str]] = None
    correlation_id: Optional[str] = None


class RecognizeBody(BaseModel):
    image_id: Optional[str] = None
    image_b64: Optional[str] = None
    prompt: str = ""
    lang: str = "eng"
    mode: str = "accurate"
    response_delivery: Optional[ResponseDelivery] = None


def _validate_recognize_body(body: RecognizeBody) -> Optional[JSONResponse]:
    if len(body.prompt) > _MAX_PROMPT:
        return _err(400, "prompt_too_long", f"prompt exceeds {_MAX_PROMPT} characters")
    if bool(body.image_id) == bool(body.image_b64):
        return _err(
            400,
            "invalid_arguments",
            "Exactly one of image_id or image_b64 must be set",
        )
    if body.mode not in ("fast", "accurate"):
        return _err(400, "invalid_arguments", "mode must be fast or accurate")
    return None


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@router.get("/ocr/health")
async def ocr_health(settings: Settings = Depends(_settings)):
    return build_ocr_health(settings)


@router.get("/ocr/models/{name}")
async def ocr_model_download(name: str, settings: Settings = Depends(_settings)):
    if not _MODEL_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid model name")
    base = Path(settings.jarvis_ocr_models_dir).resolve()
    path = (base / name).resolve()
    if not str(path).startswith(str(base)) or not path.is_file():
        raise HTTPException(status_code=404, detail="Unknown model")
    return FileResponse(path, filename=name)


@router.post("/ocr/recognize")
async def ocr_recognize(
    request: Request,
    body: RecognizeBody,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    settings: Settings = Depends(_settings),
    store: ImageStore = Depends(_image_store),
):
    """Structured OCR + object detection (same contract as MCP `jarvis_ocr_recognize`)."""
    sem: asyncio.Semaphore = request.app.state.ocr_recognize_semaphore
    try:
        await asyncio.wait_for(
            sem.acquire(),
            timeout=settings.jarvis_ocr_acquire_timeout_s,
        )
    except asyncio.TimeoutError:
        return _err(
            503,
            "ocr_overloaded",
            "Too many concurrent OCR requests; try again later",
        )
    t0 = time.perf_counter()
    try:
        resp = await _ocr_recognize_impl(
            request, body, idempotency_key, settings, store
        )
    finally:
        sem.release()
    record_ocr_recognize(resp.status_code, time.perf_counter() - t0)
    return resp


async def _ocr_recognize_impl(
    request: Request,
    body: RecognizeBody,
    idempotency_key: Optional[str],
    settings: Settings,
    store: ImageStore,
) -> JSONResponse:
    bad = _validate_recognize_body(body)
    if bad:
        return bad

    store_api = request.app.state.idempotency_store
    body_dict = body.model_dump(mode="json")
    body_hash = canonical_body_hash(body_dict)

    if idempotency_key:
        existing = store_api.get(idempotency_key)
        if existing:
            if existing.body_hash != body_hash:
                return _err(
                    409,
                    "idempotency_conflict",
                    "Idempotency-Key was reused with a different request body",
                )
            if existing.response is not None:
                return JSONResponse(content=existing.response)

    if body.response_delivery:
        try:
            validate_relay_target(body.response_delivery.url, settings)
        except RelayError as e:
            return _err(400, "invalid_relay", f"{e.code}: {e}")

    image_bytes: bytes
    resolved_image_id: Optional[str] = None
    if body.image_id:
        if not is_uuid_v4(body.image_id):
            return _err(400, "invalid_arguments", "image_id must be UUID v4")
        rec = store.get(body.image_id)
        if not rec:
            return _err(404, "image_not_found", "Unknown or expired image_id")
        image_bytes = rec.path.read_bytes()
        resolved_image_id = body.image_id
    else:
        assert body.image_b64 is not None
        try:
            image_bytes = base64.b64decode(body.image_b64, validate=True)
        except (ValueError, TypeError):
            return _err(400, "invalid_arguments", "image_b64 is not valid base64")
        if len(image_bytes) > settings.jarvis_image_max_bytes:
            return _err(413, "image_too_large", "Decoded image exceeds configured max bytes")

    req_id = str(uuid.uuid4())
    received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        result, meta = await run_recognition(
            image_bytes,
            body.prompt,
            body.lang,
            body.mode,
            settings,
        )
    except RuntimeError as e:
        if str(e) == "paddleocr_not_installed":
            return _err(503, "engine_unavailable", "PaddleOCR is not installed or OCR engine failed")
        return _err(503, "engine_unavailable", str(e))
    except Exception as e:
        return _err(503, "engine_unavailable", str(e))

    if not (result.get("text") or "").strip() and not any(
        (b.get("text") or "").strip() for b in (result.get("blocks") or [])
    ):
        return _err(422, "no_text_detected", "OCR produced no text")

    envelope: Dict[str, Any] = {
        "request": {
            "id": req_id,
            "received_at": received_at,
            "image_id": resolved_image_id,
            "prompt": body.prompt,
        },
        "result": result,
        "meta": meta,
        "delivery": None,
    }
    if body.response_delivery and body.response_delivery.correlation_id:
        envelope["meta"]["correlation_id"] = body.response_delivery.correlation_id

    if body.response_delivery:
        delivery = await deliver_webhook(
            body.response_delivery.url,
            body.response_delivery.method,
            body.response_delivery.headers,
            envelope,
            settings.jarvis_ocr_relay_timeout_s,
        )
        envelope["delivery"] = delivery
    else:
        envelope["delivery"] = {"attempted": False, "status_code": None, "error": None}

    if idempotency_key:
        store_api.set(
            idempotency_key,
            body_hash,
            req_id,
            "success",
            envelope,
            None,
            settings.idempotency_ttl_seconds,
        )

    return JSONResponse(content=envelope)
