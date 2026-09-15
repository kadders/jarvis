from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional

from utils.config import Settings

_MAX_SHA_BYTES = 64 * 1024 * 1024


def _paddleocr_version() -> str:
    try:
        return metadata.version("paddleocr")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _onnx_artifact_meta(path: Path) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "size_bytes": None,
        "mtime_utc": None,
        "sha256": None,
    }
    if not path.is_file():
        return meta
    st = path.stat()
    meta["size_bytes"] = int(st.st_size)
    meta["mtime_utc"] = datetime.fromtimestamp(
        st.st_mtime, tz=timezone.utc
    ).isoformat()
    if st.st_size <= _MAX_SHA_BYTES:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        meta["sha256"] = h.hexdigest()
    return meta


def build_ocr_health(settings: Settings) -> Dict[str, Any]:
    """Aggregate OCR + detector readiness for GET /api/v1/ocr/health."""
    engine = settings.jarvis_ocr_engine
    detector_mode = settings.jarvis_ocr_detector
    det_path = Path(settings.jarvis_ocr_yolo_onnx_path)
    if detector_mode == "mock":
        detector_ready = True
    else:
        detector_ready = det_path.is_file()
    if engine == "mock":
        ocr_ready = True
        engine_name = "mock"
        version = "0.0.0"
    else:
        try:
            import paddle  # noqa: F401

            ocr_ready = True
            engine_name = "paddleocr"
            version = _paddleocr_version()
        except ImportError:
            ocr_ready = False
            engine_name = "paddleocr"
            version = "0.0.0"

    ready = ocr_ready and detector_ready
    artifact = _onnx_artifact_meta(det_path)
    operator_version: Optional[str] = settings.jarvis_ocr_detector_version
    return {
        "status": "ok" if ready else "degraded",
        "engine": engine_name,
        "version": version,
        "ready": ready,
        "queue_depth": 0,
        "concurrency_limit": max(1, settings.jarvis_ocr_max_concurrent),
        "detector": {
            "name": "yolov8n_onnx",
            "mode": detector_mode,
            "ready": detector_ready,
            "path": str(det_path),
            "artifact_size_bytes": artifact["size_bytes"],
            "artifact_mtime_utc": artifact["mtime_utc"],
            "artifact_sha256": artifact["sha256"],
            "operator_version": operator_version,
        },
    }
