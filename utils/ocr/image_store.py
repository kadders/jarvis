from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger


@dataclass
class StoredImage:
    image_id: str
    path: Path
    meta_path: Path
    content_type: str
    expires_at: float  # unix time


class ImageStore:
    """Filesystem-backed ephemeral images with JSON sidecar metadata."""

    def __init__(self, base_dir: str, ttl_seconds: int) -> None:
        self.base = Path(base_dir)
        self.ttl_seconds = ttl_seconds
        self.base.mkdir(parents=True, exist_ok=True)

    def _paths(self, image_id: str) -> Tuple[Path, Path]:
        safe = image_id.lower()
        return self.base / f"{safe}.bin", self.base / f"{safe}.json"

    def save(
        self, data: bytes, content_type: str, image_id: Optional[str] = None
    ) -> StoredImage:
        iid = image_id or str(uuid.uuid4())
        path, meta_path = self._paths(iid)
        expires_at = time.time() + self.ttl_seconds
        meta = {
            "image_id": iid,
            "content_type": content_type,
            "size_bytes": len(data),
            "expires_at": expires_at,
        }
        path.write_bytes(data)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return StoredImage(
            image_id=iid,
            path=path,
            meta_path=meta_path,
            content_type=content_type,
            expires_at=expires_at,
        )

    def get(self, image_id: str) -> Optional[StoredImage]:
        path, meta_path = self._paths(image_id)
        if not path.is_file() or not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._unlink_pair(path, meta_path)
            return None
        expires_at = float(meta.get("expires_at", 0))
        if time.time() > expires_at:
            self._unlink_pair(path, meta_path)
            return None
        return StoredImage(
            image_id=meta.get("image_id", image_id),
            path=path,
            meta_path=meta_path,
            content_type=str(meta.get("content_type", "application/octet-stream")),
            expires_at=expires_at,
        )

    def delete(self, image_id: str) -> None:
        path, meta_path = self._paths(image_id)
        self._unlink_pair(path, meta_path)

    def sweep_expired(self, batch: int = 200) -> int:
        """Remove expired image pairs by scanning sidecars (best-effort, capped)."""
        removed = 0
        now = time.time()
        try:
            metas = sorted(self.base.glob("*.json"))
        except OSError as e:
            logger.warning(f"image store sweep list {self.base}: {e}")
            return 0
        for meta_path in metas:
            if removed >= max(1, batch):
                break
            path = meta_path.with_suffix(".bin")
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._unlink_pair(path, meta_path)
                removed += 1
                continue
            expires_at = float(meta.get("expires_at", 0))
            if now > expires_at:
                self._unlink_pair(path, meta_path)
                removed += 1
        if removed:
            logger.info(f"image store sweep removed={removed} (batch cap={batch})")
        return removed

    @staticmethod
    def _unlink_pair(path: Path, meta_path: Path) -> None:
        for p in (path, meta_path):
            try:
                if p.is_file():
                    p.unlink()
            except OSError as e:
                logger.warning(f"image store unlink {p}: {e}")
