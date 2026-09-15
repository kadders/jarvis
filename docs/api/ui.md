# Chat UI

Browser chat front-end for the Jarvis LLM stack. A small **BFF** on port **8003** serves a static SPA and proxies chat/health to the LLM API so the browser never talks to Ollama or `:8000` directly.

## Endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/` | Chat SPA (`ui/static/`). Hash routes: `#chat` (default), `#learning`. |
| `GET` | `/health` | UI process health. |
| `GET` | `/api/health` | Aggregated UI + LLM API health (`llm`: `ok` \| `down`). |
| `POST` | `/api/chat` | Send a turn; returns model reply + meta. |
| `POST` | `/api/feedback` | Proxy thumbs / corrections to LLM **`POST /api/v1/feedback`**. |
| `GET` | `/api/feedback/summary` | Proxy LLM **`GET /api/v1/feedback/summary`** (correctness slices). |
| `GET` | `/api/feedback/list` | Proxy LLM **`GET /api/v1/feedback`** (list / filters). |
| `GET` | `/api/feedback/item/{id}` | Proxy LLM **`GET /api/v1/feedback/{id}`** (full record). |

### Learning view

Masthead **Chat** | **Learning**. The Learning view shows correctness by slice (all / ask-local / agent outcomes / MCP), a by-day strip, filters (slice, rating defaulting to Incorrect, source, search), and a list/detail split for prompt / model answer / correction. Refresh reloads summary + list. See [Feedback and fine-tuning](../operations/feedback-and-finetune.md#learning-ui).

### `POST /api/chat`

```json
{
  "message": "explain Pascal CUDA",
  "model_tier": "fast",
  "history": [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "Hello."}
  ]
}
```

The BFF flattens `history` + `message` into a single prompt, then calls **`POST /api/v1/events`** with:

- `event_type`: `text_query`
- `content.text`: the flattened prompt
- `metadata.model_tier`: `default` or `fast`
- header **`Idempotency-Key`**: fresh UUID per request

Response:

```json
{
  "reply": "...",
  "event_id": "...",
  "meta": {
    "tier": "fast",
    "timing_ms": 1234,
    "backend": "ollama",
    "model": "llama3.2:3b"
  }
}
```

## Run (Compose)

```bash
docker compose up -d ui_service
# open http://127.0.0.1:8003/
```

Requires **`llm_api`** (and Ollama models pulled). Env: **`JARVIS_LLM_BASE_URL=http://llm_api:8000`**.

## Run (local)

```bash
cd ui
pip install -r requirements.txt
export JARVIS_LLM_BASE_URL=http://127.0.0.1:8000
uvicorn main:app --host 0.0.0.0 --port 8003 --reload
```

## Kubernetes

Optional (not in the root LLM stack). Image **`jarvis_ui`**.

```bash
# Operator — gpu-rig / jarvis-llm (preferred):
docker build -t 192.168.86.120:32000/jarvis_ui:latest -f ui/Dockerfile ./ui
docker push 192.168.86.120:32000/jarvis_ui:latest
kubectl apply -k k8s/overlays/gpu-rig-ui/
kubectl -n jarvis-llm port-forward svc/ui 8003:8003
```

ConfigMap **`jarvis-ui-config`** uses the standard [ConfigMap hot-reload](../operations/kubernetes-config-hot-reload.md) pattern. Point **`JARVIS_LLM_BASE_URL`** at the in-cluster LLM API service (`http://llm-api:8000`).

## Environment

See [Environment variables](../reference/environment-variables.md#chat-ui).

## Related

- [LLM events API](llm-events.md) — underlying contract
- [LLM tiers and hardware](../operations/llm-tiers-and-hardware.md) — Big vs Fast models
