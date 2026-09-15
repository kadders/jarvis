# MCP bridge (`jarvis_mcp`)

The **`jarvis_mcp`** package exposes Jarvis over the [Model Context Protocol](https://modelcontextprotocol.io/). **`JARVIS_MCP_TRANSPORT`** selects **stdio** (Cursor / CLI), **streamable-http** (embedded / remote HTTP), or **both** (HTTP in a background thread + stdio). Full argument and return shapes: **[MCP tool payloads](mcp-payloads.md)**.

## Run

**Ollama is not called by MCP directly.** `jarvis_mcp` sends HTTP to the **Jarvis LLM API** (`JARVIS_LLM_BASE_URL`, default `http://127.0.0.1:8000/api/v1`). The API’s `LLMHandler` then calls **Ollama** using `OLLAMA_BASE_URL` / `OLLAMA_FAST_BASE_URL` (OpenAI-compatible `/v1`). Pull models on the **Ollama** container(s), not on the MCP host.

```bash
export JARVIS_LLM_BASE_URL=http://localhost:8000/api/v1
python -m jarvis_mcp
```

### Cursor IDE / CLI (`mcp.json`)

**stdio** is the Cursor-friendly transport. Config can live in three places (all merge; project overrides are useful for repo-specific env):

| Scope | Path |
| ----- | ---- |
| **User (global)** | `~/.cursor/mcp.json` — available in every workspace |
| **This repo** | `.cursor/mcp.json` at the jarvis repo root |
| **Sibling NeXT** | `NeXT/.cursor/mcp.json` (same server entry) |

Example (paths assume this lab checkout):

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "/media/kad/data/repositories/jarvis/venv/bin/python",
      "args": ["-m", "jarvis_mcp"],
      "cwd": "/media/kad/data/repositories/jarvis",
      "env": {
        "JARVIS_LLM_BASE_URL": "http://llm.jarvis.lan/api/v1",
        "JARVIS_MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

Then enable in Cursor **Settings → MCP**, or CLI:

```bash
agent mcp enable jarvis
agent mcp list
agent mcp list-tools jarvis
```

For preference / RL capture from agents, use **`jarvis_log_outcome`** (call + end result + optional logic) and **`jarvis_submit_feedback`** (see [Feedback and fine-tuning](operations/feedback-and-finetune.md)). Repo rule **`.cursor/rules/jarvis-mcp-rl.mdc`** reminds agents to log outcomes and submit corrections. Project **`.cursor/hooks.json`** auto-mirrors MCP tool results and agent-stop transcripts into the same feedback store.

Optional:

- **`JARVIS_OCR_API_BASE_URL`** — defaults to **`JARVIS_LLM_BASE_URL`** (OCR lives on the same FastAPI app under `/api/v1`).
- **`JARVIS_ASR_BASE_URL`** — ASR service base URL (e.g. `http://localhost:8001/api/v1`)
- **`JARVIS_LLM_TIMEOUT_S`**, **`JARVIS_LLM_MAX_RETRIES`** — HTTP client resilience
- **`JARVIS_MCP_TRANSPORT`**, **`JARVIS_MCP_HOST`**, **`JARVIS_MCP_PORT`**, **`JARVIS_MCP_PATH`** — see [MCP tool payloads — transport](mcp-payloads.md#mcp-server-transport)

## Tools (summary)

| Tool | Purpose |
| ---- | ------- |
| **`jarvis_submit_event`** | `POST /events` with JSON body; always sets **`prefer_local`** + **`source: mcp`** so the local Ollama model is used and cloud fallback is skipped. |
| **`jarvis_ask_local`** | Convenience **`text_query`** on the local model; returns `event_id` + reply + `learning_applied`. |
| **`jarvis_get_event_status`** | `GET /events/{event_id}` |
| **`jarvis_submit_feedback`** | `POST /feedback` — ratings / corrections for local-model learning |
| **`jarvis_log_outcome`** | `POST /feedback` — call + end result (+ logic) trajectory for training |
| **`jarvis_feedback_stats`** | `GET /feedback/stats` |
| **`jarvis_health`** | `GET /health` on the LLM API |
| **`jarvis_monitor_start`** / **`jarvis_monitor_stop`** | ASR directory monitor control |
| **`jarvis_ocr_recognize`** | OCR + object detection via `POST /api/v1/ocr/recognize` — see [OCR service](services/ocr.md) |
| **`jarvis_ocr_health`** | `GET /api/v1/ocr/health` |

## Idempotency and retries

`jarvis_submit_event` and `jarvis_ocr_recognize` retry transient HTTP failures with backoff. Retries reuse the **same** `Idempotency-Key` header value (host-provided key or a generated UUID for the tool call).

## See also

- [MCP tool payloads](mcp-payloads.md)
- [LLM events API](api/llm-events.md)
- [OCR service (spec)](services/ocr.md)

## Revision note

**2026-05-14** — Added **`docs/mcp-payloads.md`**, **`jarvis_ocr_*`** tools, **`JARVIS_MCP_TRANSPORT`** modes, and alignment with **`POST /api/v1/ocr/recognize`** (see [OCR spec revision history](services/ocr.md#spec-revision-history)).

**Later** — **`jarvis_submit_event`** forwards optional **`metadata`** (e.g. **`model_tier: "fast"`**) so MCP traffic can target the fast Ollama tier without a second MCP URL.

**2026-07-28** — Added **`jarvis_log_outcome`** and Cursor **`.cursor/hooks.json`** (`afterMCPExecution` / `stop`) so call + end-result trajectories are posted to **`POST /api/v1/feedback`** for local-model training.
