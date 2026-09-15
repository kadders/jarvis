# OpenAI-compatible API (`/v1`)

Jarvis exposes an **OpenAI-compatible** surface so clients such as **Cursor** (Override OpenAI Base URL), SDKs, and curl can talk to the same models as `POST /api/v1/events`.

| Method | Path | Role |
| ------ | ---- | ---- |
| **`GET`** | **`/v1/models`** | List default, fast, and optional tuned model ids (plus Cursor-friendly aliases). |
| **`GET`** | **`/v1/models/{id}`** | Single model metadata. |
| **`POST`** | **`/v1/chat/completions`** | Chat completion; supports **`stream: true`** (SSE). |

The legacy events API remains at **`/api/v1/events`**. Prefer **`/v1`** for IDE / OpenAI clients.

## Auth

If **`JARVIS_OPENAI_API_KEY`** is set, `/v1/*` requires:

```http
Authorization: Bearer <key>
```

or header **`x-api-key: <key>`**. Events/OCR under `/api/v1` stay unchanged. The gpu-rig-llm overlay sets a LAN key by default (change it in the ConfigMap).

## Model ids and aliases

Canonical Ollama tags (e.g. **`llama3.1:8b`**, **`llama3.2:3b`**) are listed. Jarvis also publishes **sanitized aliases** (e.g. **`llama31-8b`**) because some Cursor builds strip `:` / `-` from custom model names. Use whichever the client accepts; both resolve to the same tier.

Optional **`MODEL_NAME_TUNED`** (e.g. **`jarvis-tuned:latest`**) appears after a DPO/QLoRA promote.

## Non-streaming example

```bash
curl -s http://llm.jarvis.lan/v1/chat/completions \
  -H "Authorization: Bearer jarvis-lan-openai" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama31-8b",
    "messages": [{"role": "user", "content": "Say hello"}],
    "stream": false
  }'
```

Response shape matches OpenAI (`id`, `choices[0].message`, `usage`) plus a small **`jarvis`** object (`tier`, `backend`, `timing_ms`).

## Streaming

Set **`stream": true`**. The response is **`text/event-stream`** with `data: {...}` chunks and a final `data: [DONE]`.

## Cursor on the LAN {#cursor}

1. Apply **`k8s/overlays/gpu-rig-llm/`** (MetalLB **`192.168.86.10`**) and refresh DNS (**`llm.jarvis.lan`**).
2. Cursor **Settings → Models**:
   - Enable **Override OpenAI Base URL** → `http://llm.jarvis.lan/v1` (or `http://192.168.86.10/v1`).
   - API key = value of **`JARVIS_OPENAI_API_KEY`** (overlay default `jarvis-lan-openai`).
   - **Add model** using a listed id / alias (e.g. `llama31-8b`).
   - Prefer that model in the chat/agent picker for day-to-day work.
3. Verify from a LAN host first:

```bash
curl -s http://llm.jarvis.lan/v1/models \
  -H "Authorization: Bearer jarvis-lan-openai"
```

**Note:** Cursor Tab autocomplete stays on Cursor’s cloud. Chat / Cmd+K / Agent can use this endpoint. If Cursor’s BYOK path cannot reach LAN HTTP, use a tunnel (see [Cursor tunnel fallback](../operations/cursor-tunnel.md)).

### Primary Jarvis + automatic cloud fallback {#cursor-fallback}

Cursor does **not** auto-switch from a custom OpenAI base URL back to Cursor Pro models when the custom endpoint fails — **Override OpenAI Base URL** is global. To get “Jarvis first, cloud if broken” without toggling settings:

1. Keep Cursor pointed **only** at Jarvis `/v1`.
2. Enable Jarvis **cloud fallback** (OpenAI-compatible upstream) so Ollama failures transparently retry against e.g. **`gpt-4o-mini`**:

| Variable | Example |
| -------- | ------- |
| **`JARVIS_FALLBACK_ENABLED`** | `true` |
| **`JARVIS_FALLBACK_BASE_URL`** | `https://api.openai.com/v1` |
| **`JARVIS_FALLBACK_API_KEY`** | your OpenAI (or compatible) key |
| **`JARVIS_FALLBACK_MODEL`** | `gpt-4o-mini` |
| **`JARVIS_FALLBACK_TIMEOUT_S`** | `60` |

Successful fallback responses include `"jarvis": {"fallback": true, "backend": "fallback", ...}` and increment **`jarvis_llm_fallback_total`**.

This is **not** Cursor Pro / Composer billing — it is whatever API key you put in **`JARVIS_FALLBACK_API_KEY`**. To use Cursor’s built-in models instead, turn **off** Override OpenAI Base URL and pick a Cursor model (manual).

### Manual Cursor Pro switch

When you want Claude / Composer / other Cursor-hosted models: disable Override OpenAI Base URL (and optionally clear the custom OpenAI key), then select the Cursor model. Re-enable the override to return to Jarvis.

## Feedback

Completion ids (`chatcmpl-…`) can be rated via **`POST /api/v1/feedback`** — see [Feedback and fine-tuning](../operations/feedback-and-finetune.md).

## Related

- [LLM events API](llm-events.md)
- [Cluster DNS](../operations/cluster-dns.md)
- [LLM tiers and hardware](../operations/llm-tiers-and-hardware.md)
