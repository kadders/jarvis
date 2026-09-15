from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from utils.config import Settings, get_settings
from utils.ocr.image_store import ImageStore
from utils.ocr.uuid4 import is_uuid_v4

router = APIRouter(tags=["images"])


def _image_store(request: Request) -> ImageStore:
    return request.app.state.image_store


def _settings() -> Settings:
    return get_settings()


class ImageUploadResponse(BaseModel):
    image_id: str
    size_bytes: int
    content_type: str
    expires_at: str


def _upload_body_hash(content_type: str, data: bytes) -> str:
    h = hashlib.sha256()
    h.update(content_type.encode("utf-8"))
    h.update(b"\0")
    h.update(data)
    return h.hexdigest()


@router.post("/images", response_model=ImageUploadResponse, status_code=201)
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    settings: Settings = Depends(_settings),
    store: ImageStore = Depends(_image_store),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    ct = file.content_type or ""
    if ct not in ("image/jpeg", "image/jpg", "image/png"):
        raise HTTPException(
            status_code=415,
            detail="Unsupported media type; use image/jpeg or image/png",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > settings.jarvis_image_max_bytes:
        raise HTTPException(status_code=413, detail="File too large")

    # Basic magic-byte check
    if not (
        (data[:2] == b"\xff\xd8" and "jpeg" in ct)
        or (data[:8] == b"\x89PNG\r\n\x1a\n" and "png" in ct)
    ):
        raise HTTPException(status_code=400, detail="Content does not match declared image type")

    body_hash = _upload_body_hash(ct, data)
    store_api = request.app.state.idempotency_store

    if idempotency_key:
        existing = store_api.get(idempotency_key)
        if existing:
            if existing.body_hash != body_hash:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was reused with different image bytes",
                )
            resp = existing.response or {}
            if isinstance(resp, dict) and "image_id" in resp:
                return ImageUploadResponse(**resp)
            raise HTTPException(status_code=500, detail="Corrupt idempotency record for image")

    rec = store.save(data, content_type=ct)
    expires_iso = datetime.fromtimestamp(rec.expires_at, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    out = ImageUploadResponse(
        image_id=rec.image_id,
        size_bytes=len(data),
        content_type=ct,
        expires_at=expires_iso,
    )
    if idempotency_key:
        store_api.set(
            idempotency_key,
            body_hash,
            rec.image_id,
            "created",
            out.model_dump(),
            None,
            settings.jarvis_image_ttl_s,
        )
    return out


@router.delete("/images/{image_id}", status_code=204)
async def delete_image(
    image_id: str,
    store: ImageStore = Depends(_image_store),
):
    if not is_uuid_v4(image_id):
        raise HTTPException(status_code=400, detail="image_id must be a UUID v4")
    store.delete(image_id)
    return None
