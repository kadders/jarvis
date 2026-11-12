# MCP tool payload reference

Jarvis MCP tools return a **JSON string** (MCP `text` content). Parse with `JSON.parse` / `json.loads` on the host.

**Normative OCR + REST:** [OCR service](services/ocr.md)  
**LLM events HTTP (for comparison):** [LLM events API](api/llm-events.md)

---

## Shared HTTP behavior

| Concern | Behavior |
| ------- | -------- |
| Retries | Transient **429**, **502**, **503**, **504** — exponential backoff, up to **`JARVIS_LLM_MAX_RETRIES`** (default **3**). |
| Timeouts | **`JARVIS_LLM_TIMEOUT_S`** (default **120** s) for outbound HTTP from MCP. |
| Idempotency | **`jarvis_submit_event`** and **`jarvis_ocr_recognize`** send **`Idempotency-Key`** (caller-provided or generated UUID per call). |

---

## `jarvis_submit_event`

| Argument | Type | Required | Notes |
| -------- | ---- | -------- | ----- |
| `event_type` | string | yes | Forwarded in JSON body. |
| `content` | object | yes | Arbitrary JSON object. |
| `metadata` | object | no | Optional; include **`{"model_tier": "fast"}`** for the fast Ollama tier. MCP always merges **`source: "mcp"`** and **`prefer_local: true`**. |
| `idempotency_key` | string | no | Replayed on HTTP **`Idempotency-Key`**. |

**HTTP:** `POST {JARVIS_LLM_BASE_URL}/events`

**Success return:** Same JSON as **`POST /api/v1/events`** (see [LLM events](api/llm-events.md)). Response **`meta.learning_applied`** is true when prior corrections were injected into the local prompt.

**Errors:** On HTTP **4xx/5xx**, tools return a **JSON string** (not a thrown transport error) with shape **`http_error`** (see [HTTP tool errors](#http-tool-errors) below). **409** if idempotency key reused with different body.

---

## `jarvis_get_event_status`

| Argument | Type | Required |
| -------- | ---- | -------- |
| `event_id` | string | yes |

**HTTP:** `GET {JARVIS_LLM_BASE_URL}/events/{event_id}`

**Success return:** Event status JSON.

**Not found:** JSON string `{"error":"not_found","event_id":"..."}` when the upstream returns **404**.

**Other HTTP errors:** Structured **`http_error`** envelope (see [HTTP tool errors](#http-tool-errors)).

---

## `jarvis_ask_local`

| Argument | Type | Required | Notes |
| -------- | ---- | -------- | ----- |
| `text` | string | yes | User question |
| `model_tier` | string | no | `default` or `fast` |
| `idempotency_key` | string | no | |

**HTTP:** same as **`jarvis_submit_event`** with `event_type=text_query`, `metadata.source=mcp`, **`prefer_local=true`**.

**Success return:** Events API JSON. Use **`event_id`** with **`jarvis_submit_feedback`**. Check **`response.meta.learning_applied`**.

---

## `jarvis_submit_feedback`

| Argument | Type | Required | Notes |
| -------- | ---- | -------- | ----- |
| `rating` | int | yes | `-1`, `0`, or `1` |
| `event_id` | string | no | Jarvis events id |
| `completion_id` | string | no | OpenAI `chatcmpl-…` id |
| `correction` | string | no | Preferred answer text |
| `preferred` / `rejected` | string | no | Explicit DPO pair |
| `prompt` / `response` | string | no | Override when ids are unknown |
| `domain` | string | no | `python_jarvis` \| `arduino_wand` \| `rrf_nxt` \| `cad` \| `mentra` \| `general` |

**HTTP:** `POST {JARVIS_LLM_BASE_URL}/feedback` with `source: "mcp"`.

**Success return:** `{"status":"ok","feedback_id":"...","created_at":…}`.

**HTTP errors:** Structured **`http_error`** envelope (see [HTTP tool errors](#http-tool-errors)).

---

## `jarvis_log_outcome`

Log a **call + end result** (optional brief **logic**) into the feedback store so agents and operators can follow trajectories and train the local model.

| Argument | Type | Required | Notes |
| -------- | ---- | -------- | ----- |
| `call` | string | yes | What was asked / the task (stored as `prompt`) |
| `result` | string | yes | End result / answer (stored as `response`) |
| `rating` | int | no | Default **`0`** (observation). **`1`** good, **`-1`** bad |
| `logic` | string | no | Short reasoning / steps (stored in `metadata.logic`) |
| `correction` | string | no | Preferred text when `rating` is **`-1`** |
| `event_id` | string | no | Link to a prior Jarvis event |
| `completion_id` | string | no | Link to an OpenAI `chatcmpl-…` id |
| `domain` | string | no | `python_jarvis` \| `arduino_wand` \| `rrf_nxt` \| `cad` \| `mentra` \| `general` (stored in `metadata.domain`) |

**HTTP:** `POST {JARVIS_LLM_BASE_URL}/feedback` with `source: "mcp"`, `metadata.kind: "outcome"`. When `rating > 0` and no `correction`, sets **`preferred`** to `result` for later DPO export.

**Success return:** Same as **`jarvis_submit_feedback`**.

**HTTP errors:** Structured **`http_error`** envelope (see [HTTP tool errors](#http-tool-errors)).

Cursor project hooks (`.cursor/hooks.json`) also auto-post MCP results and agent-stop transcripts to the same endpoint (`source: "cursor"`). Prefer an explicit **`jarvis_log_outcome`** when you want a clean call→result→logic record.

---

## `jarvis_feedback_stats`

No arguments.

**HTTP:** `GET {JARVIS_LLM_BASE_URL}/feedback/stats`

**Success return:** `{"count": N, "path": "..."}`.

---

## `jarvis_health`

No arguments.

**HTTP:** `GET` on LLM API root **`/health`** (derived from `JARVIS_LLM_BASE_URL` by stripping `/api/v1`).

**Success return:** Root health JSON.

**HTTP errors:** **`http_error`** envelope when status **>= 400** (see [HTTP tool errors](#http-tool-errors)).

## `jarvis_monitor_start` / `jarvis_monitor_stop`

No arguments.

**HTTP:** `POST {JARVIS_ASR_BASE_URL}/monitor/start` or `.../stop`

**Return:** Raw response text; if **`JARVIS_ASR_BASE_URL`** unset, JSON `{"error":"JARVIS_ASR_BASE_URL not set"}`.

---

## `jarvis_ocr_recognize` {#jarvis_ocr_recognize}

| Argument | Type | Required | Notes |
| -------- | ---- | -------- | ----- |
| `prompt` | string | yes | Max **4096** chars; otherwise MCP-side JSON error `prompt_too_long`. |
| `image_id` | string | one of | UUID from **`POST /api/v1/images`**. Mutually exclusive with `image_b64`. |
| `image_b64` | string | one of | Raw base64 image bytes (JPEG/PNG). |
| `lang` | string | no | Default **`eng`**. |
| `mode` | string | no | **`fast`** or **`accurate`** (default **`accurate`**). |
| `idempotency_key` | string | no | Forwarded to **`POST /api/v1/ocr/recognize`**. |
| `response_delivery` | object | no | Client-directed webhook (see [OCR spec](services/ocr.md#response_delivery)). |

**HTTP:** `POST {JARVIS_OCR_API_BASE_URL or JARVIS_LLM_BASE_URL}/ocr/recognize` with JSON body mirroring the arguments (excluding `idempotency_key`, which is a header only).

**Success return:** Parsed JSON with **`request`**, **`result`** (includes mandatory **`objects`** array), **`meta`**, **`delivery`**.

**HTTP errors:** Status **>= 400** returns a structured **`http_error`** JSON string (see [HTTP tool errors](#http-tool-errors)), not only raw body text.

**MCP-side validation errors** (before HTTP): JSON strings such as `{"error":"invalid_arguments",...}` or `{"error":"prompt_too_long",...}`.

---

## `jarvis_ocr_health`

No arguments.

**HTTP:** `GET {JARVIS_OCR_API_BASE_URL or JARVIS_LLM_BASE_URL}/ocr/health`

**Success return:** Same JSON as **`GET /api/v1/ocr/health`** (stringified).

**HTTP errors:** **`http_error`** envelope when status **>= 400** (see [HTTP tool errors](#http-tool-errors)).

---

## HTTP tool errors {#http-tool-errors}

When an outbound HTTP call from a tool returns **status >= 400**, the tool result is a **JSON string** (parse with `json.loads`) with:

| Field | Type | Notes |
| ----- | ---- | ----- |
| `error` | string | Always **`http_error`**. |
| `status_code` | int | Upstream HTTP status. |
| `method` | string | HTTP verb used. |
| `url` | string | Full request URL. |
| `body_preview` | string | Truncated response body (text, up to ~2000 characters). |
| `request_id` | string or null | **`X-Request-ID`** from the Jarvis LLM API when the upstream response included it. |
| `response_json` | any | Only if **`Content-Type`** suggests JSON **and** the body parses as JSON. |

---

## MCP server transport {#mcp-server-transport}

Run the bridge with:

```bash
python -m jarvis_mcp
```

| Variable | Default | Description |
| -------- | ------- | ----------- |
| **`JARVIS_MCP_TRANSPORT`** | `stdio` | `stdio` — Cursor / CLI hosts. `streamable-http` — HTTP MCP only. `both` — streamable HTTP in a background thread + stdio foreground. |
| **`JARVIS_MCP_HOST`** | `0.0.0.0` | HTTP bind address (FastMCP / uvicorn). |
| **`JARVIS_MCP_PORT`** | `8765` | HTTP port. |
| **`JARVIS_MCP_PATH`** | `/mcp` | Streamable HTTP path (FastMCP `streamable_http_path`). |
| **`JARVIS_OCR_API_BASE_URL`** | *(falls back to `JARVIS_LLM_BASE_URL`)* | Base including **`/api/v1`** for OCR routes. |

See [MCP bridge](mcp.md) and [OCR service](services/ocr.md) for security (Origin, bearer tokens) and deployment notes.
