# Docker and Compose

## Services

The project **`docker-compose.yml`** (repository root) defines:

- **`redis`** — Redis 7 (Alpine) with AOF persistence under **`cache/redis/data`** and a **`healthcheck`** (`redis-cli ping`).
- **`ollama`** — Ollama server on **11434** with volume **`cache/ollama`**.
- **`llm_api`** — FastAPI / LLM on **8000**; **`MODEL_TYPE=ollama`**, **`OLLAMA_BASE_URL=http://ollama:11434/v1`**; depends on Redis (healthy) and Ollama (started).
- **`asr_service`** — ASR on **8001**.
- **`wand_service`** — Wand on **8002**.
- **`ui_service`** — Chat UI on **8003** (BFF + SPA; see [Chat UI](../api/ui.md)).
- **`jarvis_mcp`** *(optional)* — MCP **streamable HTTP** on **8765** (same image as `llm_api`); set **`JARVIS_LLM_BASE_URL=http://llm_api:8000/api/v1`**. For Cursor-style **stdio**, run **`python -m jarvis_mcp`** on the host instead of this service.
- **`mosquitto`** / **`tap_collector`** *(profile **`telemetry`**)* — MQTT broker on **1883** and tap telemetry collector on **8004** (see [Tap collector](../services/tap-collector.md)). Enable with `docker compose --profile telemetry up -d mosquitto tap_collector`.

### Idempotency and Redis

Compose sets:

- **`IDEMPOTENCY_BACKEND=redis`**
- **`REDIS_URL=redis://redis:6379/0`**

So the LLM API resolves Redis by the Compose network hostname **`redis`**.

For other local setups (host-process LLM, bare `docker run`, Kubernetes), see [Redis for idempotency](redis.md).

## Typical commands

```bash
docker compose build
docker compose up -d
```

For **LLM tier sizing**, **CPU-only** defaults, and a **multi‑GTX‑1070** rollout sketch, see [LLM tiers and hardware](llm-tiers-and-hardware.md).

## Documentation image

- **`Dockerfile.docs`** — MkDocs build + nginx.
- **`docker-compose.docs.yml`** — publish on **8080**:

  ```bash
  docker compose -f docker-compose.docs.yml up --build
  ```

## Ollama model pull

After `docker compose up`, pull the default (big) chat model once, and the fast tier if you enable **`multi-llm`**:

```bash
docker compose exec ollama ollama pull llama3.1:8b
docker compose --profile multi-llm exec ollama_fast ollama pull llama3.2:3b
```

Compose / K8s pin **`ollama/ollama:0.4.7`**. **`gpt-oss:20b`** needs a newer Ollama (HTTP **412** on 0.4.7); see [Kubernetes — model pins](kubernetes.md#model-pins-and-gpt-oss).
