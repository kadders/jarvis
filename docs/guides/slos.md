# SLOs and expectations {#slos-and-expectations}

Jarvis does **not** publish numeric SLO guarantees in software releases: latencies depend on **hardware**, **model size**, **quantization**, and **concurrency**. This page sets **qualitative expectations** so integrators can plan capacity and client timeouts.

## LLM tiers

| Tier | Typical model (defaults) | Role |
| ---- | ------------------------- | ---- |
| **Default (“big”)** | **`llama3.1:8b`** (PoC) / **`gpt-oss:20b`** (roadmap) | Current pin matches **`ollama:0.4.7`** on multi-GPU Pascal; gpt-oss needs a newer Ollama. |
| **Fast** | **`llama3.2:3b`** via **`metadata.model_tier: "fast"`** | Cheaper path for agents and MCP; StatefulSet replicas on GPU when available. |

**Expectation:** interactive **p95** for the fast tier on CPU may be **seconds to tens of seconds** depending on prompt length; the big tier on **Pascal-class** GPUs is often **tens of seconds** for long generations. Set **`JARVIS_LLM_TIMEOUT_S`** on MCP and client HTTP timeouts accordingly (see [LLM tiers and hardware](../operations/llm-tiers-and-hardware.md)).

## Idempotency vs event polling {#idempotency-vs-event-polling}

- **`POST /api/v1/events`** with **`Idempotency-Key`** + **Redis** gives **durable replay** of the **HTTP response body** across retries and multiple API replicas.
- **`GET /api/v1/events/{event_id}`** reads an **in-memory map** on the **process that handled the POST**. It is **not** replicated to other replicas and is **lost on restart**. See [LLM events API](../api/llm-events.md).

**Expectation:** treat **`GET /events/{id}`** as a **debug / same-process convenience** unless you add an external store later.

## OCR / vision

- **`POST /api/v1/ocr/recognize`** is bounded by **`JARVIS_OCR_MAX_CONCURRENT`**; under load, clients may wait or receive **`503`** `ocr_overloaded`.
- End-to-end latency grows with image size ( **`JARVIS_OCR_MAX_LONG_EDGE`** ), detector + OCR engine, and optional **webhook** delivery.

## MCP bridge

- Tools return **JSON strings**; HTTP failures use the **`http_error`** envelope (see [MCP tool payloads](../mcp-payloads.md#http-tool-errors)).
- Retries target transient **429**, **502**, **503**, **504** with backoff — see **`JARVIS_LLM_MAX_RETRIES`**.

## Observability

- Correlate logs with **`X-Request-ID`** and scrape **`/metrics`** into your chosen stack (see [Observability](../operations/observability.md)).
