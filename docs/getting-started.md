# Getting started

## Prerequisites

- Python 3.10+
- Docker and Docker Compose (recommended)

## Local API (without Docker)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export MODEL_TYPE=mock   # or ollama / huggingface
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

## Docker Compose (LLM + Redis + Ollama + ASR)

```bash
docker compose build
docker compose up -d
```

Pull the default chat model once, and the **fast** tier model if you use `--profile multi-llm`:

```bash
docker compose exec ollama ollama pull llama3.1:8b
docker compose --profile multi-llm exec ollama_fast ollama pull llama3.2:3b
```

See [LLM tiers and hardware](operations/llm-tiers-and-hardware.md) for sizing and the **five × GTX 1070** layout. Kubernetes PoC: [Kubernetes](operations/kubernetes.md) (`k8s/overlays/gpu-rig/`).

## Chat UI

With Compose up, open **http://127.0.0.1:8003/** for the browser chat (Big/Fast tier toggle). See [Chat UI](api/ui.md).

## Submit an event

```bash
curl -s -X POST http://localhost:8000/api/v1/events \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-1" \
  -d '{"event_type":"text_query","content":{"text":"Hello"}}' | jq .
```

## Documentation site

```bash
pip install -r requirements-docs.txt
mkdocs serve
```

Or build the static site image (see repository `Dockerfile.docs`).
