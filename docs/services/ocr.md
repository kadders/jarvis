# OCR service (specification)

This document is **normative** for the Jarvis OCR feature. A matching client-side description lives in the sibling **tiny-ocr** repository (`docs/ocr-strategy.md` in that checkout; not part of this MkDocs site).

**Related:** [MCP bridge](../mcp.md), [MCP tool payloads](../mcp-payloads.md). Repository layout for agents is in the **root `AGENTS.md`** file (outside `docs/`, not linked here so MkDocs `--strict` builds stay valid).

---

## Transport boundary {#transport-boundary}

| Domain | Real-time streaming | Primary API |
| ------ | ------------------- | ----------- |
| **Speech (ASR)** | **WebSocket** `GET ws://<asr-host>:<port>/api/v1/stream` on the **ASR** app (see [ASR streaming](../asr-streaming.md)) | REST under **`/api/v1/…`** on ASR |
| **Vision (OCR + objects)** | *(not via ASR)* — use **REST** per frame or batch; optional **`response_delivery`** webhook on recognize | **`POST /api/v1/ocr/recognize`** (and MCP **`jarvis_ocr_recognize`**) on the **LLM API** |

Do not send image or video frames to the ASR WebSocket; that endpoint is **audio only**.

---

## Overview

OCR uses **REST for binary image upload** and **MCP** (or the same JSON via **`POST /api/v1/ocr/recognize`**) for structured recognition, so firmware and agents share one contract without base64 image bloat in JSON-RPC when using the upload flow.

| Concern | Protocol | Surface |
| --- | --- | --- |
| Upload JPEG/PNG | REST | `POST /api/v1/images` |
| Delete temp image | REST | `DELETE /api/v1/images/{image_id}` |
| Engine / queue health | REST | `GET /api/v1/ocr/health` |
| Model artifacts (TFLite, etc.) | REST | `GET /api/v1/ocr/models/{name}` |
| Run OCR + object detection | REST | `POST /api/v1/ocr/recognize` |
| Run OCR + object detection | **MCP** | Tool `jarvis_ocr_recognize` |
| Agent-friendly health | **MCP** | Tool `jarvis_ocr_health` |

---

## REST API

### `POST /api/v1/images`

Uploads image bytes; server stores them under a new **UUID v4** key.

- **Request:** `multipart/form-data` with field **`file`** (JPEG or PNG), max **8 MB** (`JARVIS_IMAGE_MAX_BYTES`).
- **Headers (optional):** `Idempotency-Key` — reuse existing idempotency store from LLM `events` API (same key + same bytes → same `image_id` response).
- **Response `201`:**

```json
{
  "image_id": "550e8400-e29b-41d4-a716-446655440000",
  "size_bytes": 48231,
  "content_type": "image/jpeg",
  "expires_at": "2026-05-14T12:30:00Z"
}
```

- **`image_id`:** Always a freshly generated **UUID v4** (lowercase hex + hyphens). Clients must treat it as opaque.
- **Storage:** Filesystem under **`JARVIS_IMAGE_DIR`** (default `./cache/jarvis_images`). TTL default **900 s** (`JARVIS_IMAGE_TTL_S`, 15 minutes).
- **Errors:** `400` bad image, `413` too large, `415` unsupported media type, `409` idempotency conflict.

### `DELETE /api/v1/images/{image_id}`

- Deletes stored bytes for a valid UUID. **`204`** on success or if already absent (idempotent delete allowed).
- **Errors:** `400` if `image_id` is not a valid UUID format.

### `GET /api/v1/ocr/health`

```json
{
  "status": "ok",
  "engine": "paddleocr",
  "version": "2.7.0",
  "ready": true,
  "queue_depth": 0,
  "concurrency_limit": 4,
  "detector": {
    "name": "yolov8n_onnx",
    "mode": "onnx",
    "ready": true,
    "path": "/app/cache/models/yolov8n.onnx",
    "artifact_size_bytes": 12123456,
    "artifact_mtime_utc": "2026-05-01T12:00:00+00:00",
    "artifact_sha256": "…",
    "operator_version": "v1.2.3"
  }
}
```

- **`status`:** `ok` when both OCR and detector are ready; **`degraded`** when one stage is unavailable (e.g. missing ONNX file).
- **`concurrency_limit`:** Max concurrent **`POST /api/v1/ocr/recognize`** handlers (`JARVIS_OCR_MAX_CONCURRENT`). When saturated, new requests wait up to **`JARVIS_OCR_ACQUIRE_TIMEOUT_S`**, then **`503`** with error code **`ocr_overloaded`**.
- **`detector`:** Object-detection stage metadata. **`artifact_sha256`** is omitted for ONNX files larger than **64 MiB** (use **`artifact_mtime_utc`** / **`artifact_size_bytes`**). **`operator_version`** is optional metadata from **`JARVIS_OCR_DETECTOR_VERSION`** (e.g. your bundle id).

### `GET /api/v1/ocr/models/{name}`

- Serves static model bytes for tiny-ocr OTA (e.g. `fomo_text.tflite`). Files live under **`JARVIS_OCR_MODELS_DIR`**. `404` if unknown.

### `POST /api/v1/ocr/recognize`

Structured **OCR + mandatory object detection** on one image. Same JSON contract as MCP **`jarvis_ocr_recognize`** (MCP proxies here).

**Request JSON**

| Field | Required | Type | Notes |
| --- | --- | --- | --- |
| `image_id` | Upload flow: **yes** | string | UUID from `POST /api/v1/images`. Mutually exclusive with `image_b64`. |
| `image_b64` | One-shot: **yes** | string | Base64 image; omit when `image_id` is set. Decoded size ≤ **`JARVIS_IMAGE_MAX_BYTES`**. |
| `prompt` | **yes** | string | Natural-language OCR instruction. Max **4096** chars → **`400`** `prompt_too_long`. |
| `lang` | no | string | Default **`eng`**. |
| `mode` | no | string | **`fast`** \| **`accurate`** (default **`accurate`**). |
| `response_delivery` | no | object | Optional webhook (see below). |

**Headers (optional):** `Idempotency-Key` — replay same full JSON body within **`IDEMPOTENCY_TTL_SECONDS`**.

**Concurrency:** The server limits parallel recognize executions (**`JARVIS_OCR_MAX_CONCURRENT`**, default **4**). When busy, requests wait (**`JARVIS_OCR_ACQUIRE_TIMEOUT_S`**, default **60** s); if no slot frees in time, **`503`** with **`error`: `ocr_overloaded`**.

**Ephemeral images:** Orphan files are also removed by a periodic sweep (**`JARVIS_IMAGE_SWEEP_INTERVAL_S`**, default **300** s; set **`0`** to disable) in batches of **`JARVIS_IMAGE_SWEEP_BATCH`**.

**Successful `200` JSON**

```json
{
  "request": {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "received_at": "2026-05-14T12:00:00Z",
    "image_id": "550e8400-e29b-41d4-a716-446655440000",
    "prompt": "Transcribe all visible text in reading order."
  },
  "result": {
    "text": "full recognized text",
    "blocks": [
      { "text": "line 1", "bbox": [0, 0, 100, 20], "confidence": 0.93 }
    ],
    "confidence": 0.91,
    "lang": "eng",
    "objects": [
      { "label": "cup", "score": 0.71, "bbox": [120.0, 80.0, 200.0, 180.0] }
    ]
  },
  "meta": {
    "engine": "paddleocr",
    "model": "paddleocr",
    "latency_ms": 842,
    "prompt_echo_truncated": null,
    "detector": "onnx",
    "correlation_id": "optional-from-response_delivery"
  },
  "delivery": {
    "attempted": false,
    "status_code": null,
    "error": null
  }
}
```

- **`result.objects`:** **Always present** (empty array if the detector finds nothing or if the detector stage failed but OCR succeeded — see **`meta.detector_error`**).
- **`delivery`:** If **`response_delivery`** was set, **`attempted`** is **`true`** and **`status_code`** / **`error`** describe the outbound webhook result. Synchronous delivery (server waits up to **`JARVIS_OCR_RELAY_TIMEOUT_S`**).

### Response delivery {#response_delivery}

The optional JSON object **`response_delivery`** controls outbound webhook delivery after recognition.

| Field | Required | Type | Notes |
| --- | --- | --- | --- |
| `url` | **yes** | string | Webhook URL. **HTTPS** required unless **`JARVIS_OCR_RELAY_ALLOW_INSECURE=1`** (dev only). |
| `method` | no | string | **`POST`** (default) or **`PUT`**. |
| `headers` | no | object | String map merged into the relay HTTP request (client accepts forwarding risk). |
| `correlation_id` | no | string | Echoed under **`meta.correlation_id`** and inside the relay JSON payload’s **`meta`**. |

**SSRF:** Private, loopback, link-local, multicast, and reserved addresses are rejected unless **`JARVIS_OCR_RELAY_ALLOW_INSECURE=1`**. Optional host suffix gate: **`JARVIS_OCR_RELAY_HOST_SUFFIX_ALLOWLIST`** (comma-separated). No HTTP redirects followed on relay.

**REST / tool error codes (representative)**

| HTTP | `error` field | When |
| --- | --- | --- |
| `400` | `invalid_arguments` | Both/neither of `image_id` / `image_b64`, bad UUID, bad mode, invalid relay URL |
| `400` | `prompt_too_long` | Prompt longer than 4096 characters |
| `400` | `invalid_relay` | SSRF / scheme validation failure |
| `404` | `image_not_found` | Unknown or expired `image_id` |
| `409` | `idempotency_conflict` | Same `Idempotency-Key`, different body |
| `413` | `image_too_large` | Decoded `image_b64` too large |
| `422` | `no_text_detected` | OCR returned no text |
| `503` | `engine_unavailable` | Paddle/OCR engine failure |

MCP **`jarvis_ocr_recognize`** returns the same JSON as a **string**; HTTP errors are returned as response body text for the tool host to surface.

### Validation

- On every lookup, validate **`image_id`** against UUID v4 regex; reject malformed IDs with **`400`**.

---

## MCP tools

Implement in **`jarvis_mcp/server.py`** (Python package at the repository root) with the JSON string return style documented in [MCP tool payloads](../mcp-payloads.md).

### `jarvis_ocr_recognize`

See argument table under **`POST /api/v1/ocr/recognize`** and [MCP tool payloads](../mcp-payloads.md#jarvis_ocr_recognize).

**Retries (HTTP to internal services)**

Mirror `_request_with_retries` in `jarvis_mcp/server.py`: backoff on 429/502/503/504, max 3 attempts, reuse `idempotency_key`.

### `jarvis_ocr_health`

Returns the same JSON as `GET /api/v1/ocr/health` (as a string), for MCP-only agents.

---

## MCP transports

**Default: `JARVIS_MCP_TRANSPORT=stdio` for local hosts; `both` or `streamable-http` for embedded HTTP clients.**

| Mode | Use case |
| --- | --- |
| `stdio` | Cursor / local MCP hosts |
| `streamable-http` | ESP32 (`espressif/mcp-c-sdk`), remote HTTP clients |
| `both` | Streamable HTTP (background uvicorn) + stdio foreground |

**Environment variables**

| Variable | Default | Description |
| --- | --- | --- |
| `JARVIS_MCP_TRANSPORT` | `stdio` | `stdio` \| `streamable-http` \| `both` |
| `JARVIS_MCP_HOST` | `0.0.0.0` | HTTP MCP bind address |
| `JARVIS_MCP_PORT` | `8765` | HTTP MCP port |
| `JARVIS_MCP_PATH` | `/mcp` | HTTP path (FastMCP streamable HTTP) |

**Deployment note:** If a single process cannot satisfy your host, run **two processes** from the same image (shared **`JARVIS_IMAGE_DIR`** / engine module) — document the layout in [`operations/docker.md`](../operations/docker.md).

**Security**

- Validate `Origin` on HTTP MCP per [MCP transport spec](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) to mitigate DNS rebinding.
- Outside LAN, require `Authorization: Bearer <token>` on REST and MCP HTTP.

---

## Engine

- **Default OCR:** **PaddleOCR** when **`JARVIS_OCR_ENGINE=paddle`** (GPU-capable via **`JARVIS_OCR_DEVICE`** / Paddle GPU wheels in CUDA images). **`JARVIS_OCR_ENGINE=mock`** is for CI and minimal installs.
- **Mandatory object detection:** **ONNX Runtime** + YOLOv8-compatible ONNX at **`JARVIS_OCR_YOLO_ONNX_PATH`** when **`JARVIS_OCR_DETECTOR=onnx`** (CUDA EP preferred if **`JARVIS_OCR_DEVICE`** starts with `cuda`). **`JARVIS_OCR_DETECTOR=mock`** skips real inference (empty **`objects`**).
- **Pluggable:** `OcrEngine` / detector modules under `utils/ocr/` for future TensorRT, TFLite OTA, or cloud backends.
- **`prompt` handling:** Map known prompt patterns to engine flags where possible. **Post-OCR LLM** enrichment is **opt-in** via **`JARVIS_OCR_LLM_ENRICH`** (default off).
- **Input cap:** **`JARVIS_OCR_MAX_LONG_EDGE`** (default **1280**) bounds decode size for latency and VRAM.

---

## Implementation checklist

- [x] `api/routers/images.py`, `api/routers/ocr.py` — REST routes + shared image store + recognize
- [x] `jarvis_mcp` — OCR tools + `JARVIS_MCP_TRANSPORT` (`stdio` / `streamable-http` / `both`)
- [x] Docker Compose — optional **`jarvis_mcp`** service (see `docker-compose.yml`)
- [x] MkDocs — [mcp-payloads.md](../mcp-payloads.md), cross-links

---

## Spec revision history {#spec-revision-history}

| Date | Changes |
| --- | --- |
| **2026-05-14** | Added **`POST /api/v1/ocr/recognize`**, mandatory **`result.objects`**, **`response_delivery`** / **`delivery`**, extended **`GET /api/v1/ocr/health`** with **`detector`**, env vars for OCR/relay/MCP transport, MCP **`jarvis_ocr_*`** tools, and [mcp-payloads.md](../mcp-payloads.md). |
| **2026-05-14** | Recognize **concurrency limit** + **`ocr_overloaded`**, periodic **image store sweep**, **`GET /ocr/health`** detector **artifact** metadata + **`JARVIS_OCR_DETECTOR_VERSION`**. |
| **2026-05-15** | Documented **transport boundary**: vision/OCR stays on LLM API REST+MCP; **ASR WebSocket** is audio-only (**[transport boundary](#transport-boundary)**). |
