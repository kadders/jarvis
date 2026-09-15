# Environment variables

Variables are loaded from the process environment and, where supported, **`.env`**. Authoritative definitions for the LLM API live in **`utils/config.py`** (repository root). ASR settings are under **`asr/utils/config.py`**.

## Idempotency and Redis

| Variable | Description |
| -------- | ----------- |
| **`IDEMPOTENCY_BACKEND`** | `redis` — shared **`Idempotency-Key`** replay (Compose, Kubernetes). `memory` — single-process / tests. |
| **`REDIS_URL`** | Redis URL when backend is `redis`, e.g. `redis://redis:6379/0`. |
| **`IDEMPOTENCY_TTL_SECONDS`** | TTL for stored responses (default **86400**). |
| **`IDEMPOTENCY_KEY_PREFIX`** | Redis key prefix (default **`jarvis:idempotency:`**). |
| **`IDEMPOTENCY_MAX_MEMORY_ENTRIES`** | Cap for in-memory backend. |
| **`REQUIRE_IDEMPOTENCY_KEY`** | If `true`, missing header yields **400**. |

See [Redis operations](../operations/redis.md).

## LLM API

| Variable | Description |
| -------- | ----------- |
| **`MODEL_PATH`** | Local model directory for Hugging Face checkpoints. |
| **`MODEL_TYPE`** | `ollama`, `huggingface`, or `mock`. |
| **`MODEL_NAME`** | HF model id or Ollama tag for the **default ("big") tier** (K8s/Compose default **`llama3.1:8b`**; **`gpt-oss:20b`** after Ollama bump). |
| **`OLLAMA_BASE_URL`** | OpenAI-compatible base URL for the default tier (e.g. `http://ollama:11434/v1`). |
| **`MODEL_NAME_FAST`** | *(optional, hybrid)* Ollama tag for the fast tier — repository default **`llama3.2:3b`** (CPU‑feasible; good MCP / cheap traffic). |
| **`OLLAMA_FAST_BASE_URL`** | *(optional, hybrid)* Base URL for the fast Ollama tier (e.g. `http://ollama-fast:11434/v1`). |
| **`MODEL_NAME_TUNED`** | *(optional)* Ollama tag for a DPO/QLoRA-tuned model listed on **`GET /v1/models`**. |
| **`JARVIS_OPENAI_API_KEY`** | *(optional)* If set, **`/v1/*`** requires Bearer / `x-api-key`. |
| **`JARVIS_FALLBACK_ENABLED`** | If `true`, retry failed Ollama completions against a cloud OpenAI-compatible API. |
| **`JARVIS_FALLBACK_BASE_URL`** | Fallback base URL (e.g. `https://api.openai.com/v1`). |
| **`JARVIS_FALLBACK_API_KEY`** | API key for the fallback upstream (do not commit secrets). |
| **`JARVIS_FALLBACK_MODEL`** | Model id on the fallback upstream (default **`gpt-4o-mini`**). |
| **`JARVIS_FALLBACK_TIMEOUT_S`** | Fallback HTTP timeout seconds (default **`60`**). |
| **`JARVIS_FEEDBACK_DIR`** | Directory for feedback JSONL (default **`cache/llm_feedback`**). |
| **`JARVIS_FEEDBACK_MAX_INJECT`** | Max prior corrections injected as few-shot notes (default **`3`**). |
| **`TEMPERATURE`**, **`MAX_LENGTH`** | Generation controls. |
| **`API_PORT`**, **`API_HOST`**, **`API_PREFIX`** | Server binding. |
| **`LOG_LEVEL`** | Loguru level (default **`INFO`**). |
| **`LOG_JSON`** | If `true`, emit one JSON object per line (for Loki / ELK / journal parsers). |
| **`JARVIS_CONFIG_DIR`** | *(optional)* Directory of files whose **names** are env keys and **contents** are values; when set, the LLM API **polls** for kubelet-updated ConfigMap files and hot-reloads **`LLMHandler`**, idempotency store, **`ImageStore`**, and OCR semaphore. Example: **`/etc/jarvis/config`**. |
| **`JARVIS_CONFIG_RELOAD_INTERVAL_S`** | `30` | Poll interval in seconds (minimum **5** enforced in code). |

When `MODEL_NAME_FAST` **and** `OLLAMA_FAST_BASE_URL` are both set, clients can route a request to the fast tier by sending `metadata.model_tier = "fast"` in the event payload. Otherwise all traffic uses the default tier.

### Ollama (Pascal-friendly defaults set in manifests)

| Variable | Set by `k8s/ollama*` / Compose | Why |
| -------- | ------------------------------ | --- |
| **`OLLAMA_FLASH_ATTENTION`** | `"0"` | FlashAttention is slow / unsupported on Pascal (CC 6.1). |
| **`OLLAMA_KEEP_ALIVE`** | `"24h"` | Keep large models resident; reloads are expensive. |
| **`OLLAMA_NUM_PARALLEL`** | big `"1"` / fast `"2"` | Cap concurrent sequences per replica. |
| **`OLLAMA_MAX_LOADED_MODELS`** | `"1"` | Avoid swapping weights between requests. |

## ASR

| Variable | Description |
| -------- | ----------- |
| **`ASR_MODEL_PATH`**, **`ASR_MODEL_NAME`** | Model location and HF id. |
| **`ASR_API_PORT`**, **`ASR_API_HOST`** | ASR listen address. |
| **`AUDIO_INPUT_DIR`**, **`AUDIO_OUTPUT_DIR`**, **`TRANSCRIPTIONS_DIR`** | Shared volume paths. |
| **`MONITOR_AUDIO_DIR`** | If `true`, watch **`AUDIO_INPUT_DIR`** for new audio files. |
| **`FORWARD_TO_LLM`**, **`LLM_API_URL`** | Optional forwarding of completed transcriptions to the LLM API. |
| **`POLLING_INTERVAL`** | Seconds between directory polls inside the monitor helper (default **5**). |
| **`ASR_STREAM_LEGACY_BYTES`** | If `true` (default), WebSocket allows raw binary without `stream_open`. |
| **`JARVIS_CONFIG_DIR`**, **`JARVIS_CONFIG_RELOAD_INTERVAL_S`** | Same hot-reload contract as the LLM API when the mounted ConfigMap volume is used (see [ConfigMap hot-reload](../operations/kubernetes-config-hot-reload.md)). |

## Wand (`wand/` service)

| Variable | Description |
| -------- | ----------- |
| **`WAND_API_HOST`**, **`WAND_API_PORT`** | Listen address (default port **8002**). |
| **`WAND_ONTOLOGY_PATH`**, **`WAND_PARK_GESTURES_PATH`**, **`WAND_CLIPS_MANIFEST_PATH`** | Spell ontology, park gestures, YouTube manifest. |
| **`WAND_GESTURE_MODEL_PATH`** | Directory with `gesture_svm.pkl` + `gesture_meta.json` (falls back to heuristics). |
| **`WAND_ASR_BASE_URL`** | ASR service base for future audio bridging. |
| **`WAND_FUSION_ALPHA`**, **`WAND_FUSION_BETA`**, **`WAND_CAST_THRESHOLD`** | Gesture/audio fusion weights and cast threshold. |

## Tap collector (`tap_collector/`, Compose profile `telemetry`)

| Variable | Description |
| -------- | ----------- |
| **`TAP_COLLECTOR_HOST`**, **`TAP_COLLECTOR_PORT`** | Listen address (default port **8004**). |
| **`TAP_COLLECTOR_MQTT_HOST`**, **`TAP_COLLECTOR_MQTT_PORT`** | Mosquitto broker (Compose service **`mosquitto`**). |
| **`TAP_COLLECTOR_MQTT_TOPICS`** | Comma-separated subscribe filters (default **`tap/#,duet/#,cam/#`**). |
| **`TAP_COLLECTOR_SESSION_DIR`** | Persisted session JSON + NPZ (default **`cache/tap_sessions`**). |
| **`TAP_COLLECTOR_CAM_GCODE_DIR`** | Optional read-only CAM G-code share root (gpu-rig: **`/cam-gcode`** → fridge `…/CNC/CAM`). When unset, CAM basename resolution is disabled. |
| **`TAP_COLLECTOR_IDLE_TIMEOUT_S`** | Finalize sessions without `stop` after idle seconds (default **30**). |
| **`TAP_COLLECTOR_LLM_FORWARD`** | If `true`, POST analysis summaries to LLM **`/events`** as **`data_analysis`**. |
| **`JARVIS_LLM_BASE_URL`** | LLM API base including **`/api/v1`** for forward. |
| **`TAP_COLLECTOR_FLUTES`**, **`TAP_COLLECTOR_MAX_RPM`**, **`TAP_COLLECTOR_MIN_RPM`** | Defaults for session-end analysis. |
| **`TAP_COLLECTOR_SPINDLE_FREQ_HZ`** | Spindle operating frequency in Hz (default **400** → 24000 RPM). `MAX_RPM` defaults to freq×60 when unset. At 800 S/s, Nyquist equals 400 Hz — peaks near that line are treated as spindle 1× / alias suspects, not machining \(f_n\). |
| **`TAP_COLLECTOR_MQTT_EVENT_BUFFER`** | In-memory MQTT event stream ring size (default **5000**). |
| **`TAP_COLLECTOR_MODBUS_ROWS_MAX`** | Cap on persisted live Modbus rows (default **5000**). |
| **`TAP_COLLECTOR_RPM_SAMPLES_WS_MAX`** | Recent RPM samples on live WebSocket frames (default **500**). |
| **`TAP_COLLECTOR_RPM_REANALYZE_MIN_INTERVAL_S`** | Min seconds between live Modbus-triggered segment reanalyses (default **1.0**). |
| **`TAP_COLLECTOR_TRANSITION_HALF_WIDTH_S`** | Half-width (s) of tool-change / spindle start-stop exclusion windows (default **0.75**). |
| **`TAP_COLLECTOR_SPINDLE_OFF_RPM`** | Commanded RPM threshold treated as spindle off (default **50**). |

See [Tap collector](../services/tap-collector.md).

## TTS (optional `jarvis_tts` image)

| Variable | Description |
| -------- | ----------- |
| **`TTS_ENABLE_MOCK`** | If `true` (default), **`POST /api/v1/synthesize`** returns a mock placeholder. |
| **`JARVIS_CONFIG_DIR`**, **`JARVIS_CONFIG_RELOAD_INTERVAL_S`** | Hot-reload from a mounted ConfigMap directory (see [ConfigMap hot-reload](../operations/kubernetes-config-hot-reload.md)). |

## MCP (`jarvis_mcp`)

| Variable | Description |
| -------- | ----------- |
| **`JARVIS_LLM_BASE_URL`** | e.g. `http://localhost:8000/api/v1` |
| **`JARVIS_OCR_API_BASE_URL`** | *(optional)* Defaults to **`JARVIS_LLM_BASE_URL`**. Base including `/api/v1` for OCR HTTP. |
| **`JARVIS_ASR_BASE_URL`** | e.g. `http://localhost:8001/api/v1` |
| **`JARVIS_LLM_TIMEOUT_S`**, **`JARVIS_LLM_MAX_RETRIES`** | Client resilience. |
| **`JARVIS_MCP_TRANSPORT`** | `stdio` (default), `streamable-http`, or `both`. |
| **`JARVIS_MCP_HOST`**, **`JARVIS_MCP_PORT`**, **`JARVIS_MCP_PATH`** | HTTP MCP bind (**8765**, path **`/mcp`** by default). |

## OCR / images (LLM API process)

| Variable | Default | Description |
| -------- | ------- | ----------- |
| **`JARVIS_IMAGE_DIR`** | `./cache/jarvis_images` | Ephemeral upload storage. |
| **`JARVIS_IMAGE_TTL_S`** | `900` | Image expiry (seconds). |
| **`JARVIS_IMAGE_MAX_BYTES`** | `8388608` | Max upload / decoded base64 size (8 MiB). |
| **`JARVIS_OCR_MODELS_DIR`** | `./cache/models/ocr_ota` | Static files for `GET /api/v1/ocr/models/{name}`. |
| **`JARVIS_OCR_ENGINE`** | `paddle` | `paddle` or **`mock`** (tests / minimal). |
| **`JARVIS_OCR_DETECTOR`** | `onnx` | `onnx` or **`mock`**. Real ONNX requires **`JARVIS_OCR_YOLO_ONNX_PATH`**. |
| **`JARVIS_OCR_DEVICE`** | `cpu` | e.g. **`cuda:0`** for GPU (requires matching wheels). |
| **`JARVIS_OCR_MAX_LONG_EDGE`** | `1280` | Decode resize cap. |
| **`JARVIS_OCR_YOLO_ONNX_PATH`** | `./cache/models/yolov8n.onnx` | YOLOv8-compatible ONNX weights. |
| **`JARVIS_OCR_LLM_ENRICH`** | *(off)* | Set `true` for future post-OCR LLM enrichment. |
| **`JARVIS_OCR_RELAY_ALLOW_INSECURE`** | *(off)* | Allow `http://` relay URLs and relax SSRF for localhost (dev only). |
| **`JARVIS_OCR_RELAY_HOST_SUFFIX_ALLOWLIST`** | *(unset)* | Comma-separated host suffixes allowed as relay targets. |
| **`JARVIS_OCR_RELAY_TIMEOUT_S`** | `8` | Outbound webhook timeout. |
| **`JARVIS_OCR_MAX_CONCURRENT`** | `4` | Semaphore limit for parallel **`/ocr/recognize`** executions. |
| **`JARVIS_OCR_ACQUIRE_TIMEOUT_S`** | `60` | Max wait for a slot before **`503`** `ocr_overloaded`. |
| **`JARVIS_IMAGE_SWEEP_INTERVAL_S`** | `300` | Periodic orphan/expired sweep interval; **`0`** disables. |
| **`JARVIS_IMAGE_SWEEP_BATCH`** | `200` | Max image pairs examined per sweep tick. |
| **`JARVIS_OCR_DETECTOR_VERSION`** | *(unset)* | Optional string surfaced in **`GET /ocr/health`** (e.g. bundle or CI build id). |

## TTS (optional)

| Variable | Description |
| -------- | ----------- |
| **`TTS_ENABLE_MOCK`** | When `true`, stub synthesize returns placeholder text. |

## Chat UI {#chat-ui}

Settings live in **`ui/config.py`**.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| **`JARVIS_UI_HOST`** | `0.0.0.0` | Bind address. |
| **`JARVIS_UI_PORT`** | `8003` | HTTP port. |
| **`JARVIS_LLM_BASE_URL`** | `http://127.0.0.1:8000` | LLM API root (**no** `/api/v1` suffix). Compose: `http://llm_api:8000`. |
| **`JARVIS_UI_LLM_TIMEOUT_S`** | `120` | Outbound timeout for chat / health probes. |
| **`JARVIS_UI_HISTORY_CHAR_BUDGET`** | `6000` | Max characters of flattened history+message sent as `content.text`. |
| **`JARVIS_CONFIG_DIR`** | *(unset)* | Optional ConfigMap mount for hot-reload. |
| **`JARVIS_CONFIG_RELOAD_INTERVAL_S`** | `30` | Poll interval when config dir is set. |
