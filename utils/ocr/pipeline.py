from __future__ import annotations

import asyncio
import base64
import io
import time
import uuid
from typing import Any, Dict, List, Tuple

from loguru import logger
from PIL import Image

from utils.config import Settings


def _decode_image_bytes(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _resize_max_long_edge(img: Image.Image, max_long: int) -> Image.Image:
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long:
        return img
    scale = max_long / float(long_edge)
    nw, nh = int(w * scale), int(h * scale)
    return img.resize((max(nw, 1), max(nh, 1)), Image.Resampling.BILINEAR)


async def run_recognition(
    image_bytes: bytes,
    prompt: str,
    lang: str,
    mode: str,
    settings: Settings,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Run OCR + object detection in parallel (thread offload).
    Returns (result_dict, meta_dict).
    """
    t0 = time.perf_counter()
    loop = asyncio.get_event_loop()
    if settings.jarvis_ocr_engine == "mock":
        ocr_coro = loop.run_in_executor(None, _mock_ocr, image_bytes, prompt, lang, mode, settings)
    else:
        ocr_coro = loop.run_in_executor(None, _paddle_ocr, image_bytes, prompt, lang, mode, settings)

    if settings.jarvis_ocr_detector == "mock":
        det_coro = loop.run_in_executor(None, _mock_detect, image_bytes, settings)
    else:
        det_coro = loop.run_in_executor(None, _onnx_detect, image_bytes, settings)

    ocr_out, det_out = await asyncio.gather(ocr_coro, det_coro, return_exceptions=True)

    meta: Dict[str, Any] = {
        "engine": settings.jarvis_ocr_engine,
        "model": "mock" if settings.jarvis_ocr_engine == "mock" else "paddleocr",
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "prompt_echo_truncated": None,
        "detector": settings.jarvis_ocr_detector,
    }

    if isinstance(ocr_out, Exception):
        logger.exception("OCR stage failed: {}", ocr_out)
        raise ocr_out

    result: Dict[str, Any] = dict(ocr_out)
    if isinstance(det_out, Exception):
        logger.warning(f"Detector stage failed: {det_out}")
        result["objects"] = []
        meta["detector_error"] = str(det_out)
    else:
        result["objects"] = det_out

    return result, meta


def _mock_ocr(
    image_bytes: bytes, prompt: str, lang: str, mode: str, settings: Settings
) -> Dict[str, Any]:
    img = _resize_max_long_edge(_decode_image_bytes(image_bytes), settings.jarvis_ocr_max_long_edge)
    w, h = img.size
    text = f"[mock-ocr {w}x{h}]"
    return {
        "text": text,
        "blocks": [
            {"text": text, "bbox": [0, 0, min(w, 200), min(h, 32)], "confidence": 1.0},
        ],
        "confidence": 1.0,
        "lang": lang,
    }


def _paddle_ocr(
    image_bytes: bytes, prompt: str, lang: str, mode: str, settings: Settings
) -> Dict[str, Any]:
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError as e:
        raise RuntimeError("paddleocr_not_installed") from e

    use_gpu = settings.jarvis_ocr_device.startswith("cuda")
    # Mobile vs server-ish: fast uses smaller det model flag where supported
    det_limit_side_len = 736 if mode == "fast" else 960
    ocr = PaddleOCR(
        use_angle_cls=True,
        lang="ch" if lang.lower().startswith("ch") else "en",
        use_gpu=use_gpu,
        show_log=False,
        det_limit_side_len=det_limit_side_len,
    )
    img = _resize_max_long_edge(_decode_image_bytes(image_bytes), settings.jarvis_ocr_max_long_edge)
    import numpy as np

    arr = np.array(img)
    out = ocr.ocr(arr, cls=True)
    blocks: List[Dict[str, Any]] = []
    lines: List[str] = []
    confidences: List[float] = []
    if out and out[0]:
        for item in out[0]:
            if not item:
                continue
            box, (txt, conf) = item
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            bbox = [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
            blocks.append({"text": txt, "bbox": bbox, "confidence": float(conf)})
            lines.append(txt)
            confidences.append(float(conf))
    full_text = "\n".join(lines)
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return {
        "text": full_text,
        "blocks": blocks,
        "confidence": avg_conf,
        "lang": lang,
    }


def _mock_detect(image_bytes: bytes, settings: Settings) -> List[Dict[str, Any]]:
    _ = image_bytes, settings
    return []


def _onnx_detect(image_bytes: bytes, settings: Settings) -> List[Dict[str, Any]]:
    from utils.ocr.yolo_onnx import YoloOnnxDetector

    det = YoloOnnxDetector(settings)
    img = _resize_max_long_edge(_decode_image_bytes(image_bytes), settings.jarvis_ocr_max_long_edge)
    return det.predict(img)
