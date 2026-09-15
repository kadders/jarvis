# LLM events API

## Endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/api/v1/events` | Run the LLM pipeline for an event. |
| `GET` | `/api/v1/events/{event_id}` | Look up event status in the **in-memory store** of the **current API process** (see below). |

## Event status semantics (ephemeral) {#event-status-semantics}

- **`POST /api/v1/events`** returns an **`event_id`** and the full **`response`** envelope **synchronously** today; the same **`event_id`** is also stored in an **in-process dictionary** for **`GET`** lookups.
- That dictionary is **not shared across** multiple **`llm-api` replicas** and is **cleared on process restart**. For **multi-replica** deployments, rely on **`Idempotency-Key`** + **Redis** to replay the **POST** response body — not on **`GET /events/{id}`** from an arbitrary replica.
- **Idempotency** (Redis or memory) is a **separate** store from the **`events_store`** map: idempotency keys replay **cached HTTP outcomes** for **`POST /api/v1/events`**; they do not make **`GET /events/{id}`** globally durable.

See also [SLOs and expectations](../guides/slos.md#idempotency-vs-event-polling).

JSON with at least:

- `event_type` — e.g. `text_query`, `code_generation`, `data_analysis`
- `content` — arbitrary JSON payload for the handler
  - For **`code_generation`**: `description` (task text), `language` (`python` default, or `arduino` / `cpp`). Python prompts include **Black** (88-char lines); Arduino prompts include non-blocking ESP32 / PlatformIO guidance.
- `metadata` *(optional)* — free-form, including:
    - `model_tier` — `"default"` (big tier, e.g. `llama3.1:8b`) or `"fast"` (smaller hybrid tier, default **`llama3.2:3b`**). Falls back to the default tier when the fast tier is not configured.

```json
{
  "event_type": "text_query",
  "content": {"text": "explain Pascal CUDA compatibility"},
  "metadata": {"model_tier": "fast"}
}
```

## Response shape

`EventResponse` includes `event_id`, `status`, and `response`. The `response` field is a structured envelope:

- **`request`** — echo of `event_type`, `content`, and `formatted_prompt`
- **`result`** — `text`, `model`, optional `finish_reason` and `usage` (Ollama/OpenAI style)
- **`meta`** — `timing_ms`, `backend` (`ollama` | `huggingface` | `mock`), `tier` (`default` | `fast`), `error` flag

## Idempotency

Send header **`Idempotency-Key`** with `POST /api/v1/events`:

- Same key + same body → cached replay (same `event_id` and body) within TTL.
- Same key + different body → **`409 Conflict`**.

Configure with **`IDEMPOTENCY_BACKEND`** (`memory` or `redis`) and **`REDIS_URL`**. See [Redis operations](../operations/redis.md).

## Request tracing

The LLM API honors **`X-Request-ID`** / **`X-Correlation-ID`** and echoes **`X-Request-ID`** on responses. Logs include the same id when using structured logging (**`LOG_JSON`**). See [Observability](../operations/observability.md).

## Environment

See [Environment variables](../reference/environment-variables.md).
