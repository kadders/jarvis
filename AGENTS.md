# AGENTS.md — Jarvis repository

## What this is

- **LLM API** (repo root): FastAPI, `LLMHandler` (`ollama` / `huggingface` / `mock`), **OpenAI-compat** **`/v1/models`** + **`/v1/chat/completions`** (SSE streaming), Redis or memory **idempotency** for `POST /api/v1/events`, **`POST /api/v1/feedback`** (preference JSONL + few-shot correction injection; optional **`metadata.domain`**), **`GET /metrics`** (Prometheus), **`X-Request-ID`** middleware, optional **`LOG_JSON`**, optional **ConfigMap directory poll** (`JARVIS_CONFIG_DIR`) for hot reload in Kubernetes. Offline DPO/QLoRA helpers under **`training/`** (domain datasets: **`build_rrf_nxt_dataset`**, **`build_python_dataset`** (Black), **`build_arduino_dataset`**, **`fetch_public_python_data`**, **`merge_preferences`**; serve via **`export_ollama_tuned`** → **`jarvis-tuned:latest`** / **`MODEL_NAME_TUNED`**). Python style: **Black** 88 / py310 (**`pyproject.toml`**).
- **ASR** (`asr/`): separate FastAPI app; **WebSocket** **`/api/v1/stream`** for **live audio only** (see **`docs/asr-streaming.md`**). **OCR/vision does not use ASR’s socket**—recognition is REST/MCP on the LLM API (`docs/services/ocr.md`). Kubernetes **`k8s/asr`** uses ConfigMap **`envFrom`** + projected volume hot-reload (**`utils/k8s_config_reload.py`** copy under **`asr/utils/`**).
- **Wand** (`wand/`): Interactive wand FastAPI on **8002** — trajectory + incantation fusion, Pi/ESP32 edge clients, spell catalog (`docs/wand/`). Compose **`wand_service`**; Kubernetes **`k8s/wand`**.
- **Tap collector** (`tap_collector/`): Optional MQTT ingest on **8004** (Compose profile **`telemetry`** + Mosquitto) for sibling **tap-testing** accel/analysis/Modbus telemetry; live finalize writes NPZ; tool segments with Fusion feed/chipload stamps; CAM G-code resolve via read-only fridge NFS (`TAP_COLLECTOR_CAM_GCODE_DIR`, RRF `gcodes → /mnt/CAM/`); CSV/ZIP import + `/ui/` SPA; MQTT event stream; session store on fridge NFS in the gpu-rig-telemetry overlay (`docs/services/tap-collector.md`).
- **Chat UI** (`ui/`): browser chat BFF on **8003** — proxies **`POST /api/v1/events`**, thumbs/corrections via **`/api/feedback`**, tier toggle, LLM health. Compose **`ui_service`**; optional **`k8s/ui`**. Docs: **`docs/api/ui.md`**.
- **TTS** (`tts/`): optional minimal FastAPI stub; **`k8s/tts`** uses the same ConfigMap hot-reload pattern (**`tts/k8s_config_reload.py`** copy).
- **MCP** (`jarvis_mcp/`): stdio / streamable HTTP (see **`JARVIS_MCP_TRANSPORT`**); **`JARVIS_LLM_BASE_URL`** points at `.../api/v1`. Tool args and return shapes: **`docs/mcp-payloads.md`**; normative OCR: **`docs/services/ocr.md`**. Agents should **`jarvis_log_outcome`** (call + result + logic) for training; project Cursor hooks also auto-post MCP results and stop transcripts to feedback.
- **Docs**: MkDocs under `docs/`; **`Dockerfile.docs`** builds static HTML.
- **Kubernetes**: `k8s/` per app (`redis`, `ollama`, `llm-api`, optional `asr`, `wand`, `ui`, `mosquitto`, `tap-collector`, `cluster-dns`, `storage`, `docs`, `tts`, `llm-train`). LAN names via overlay **`k8s/overlays/gpu-rig-dns/`**; OpenAI LAN LB via **`k8s/overlays/gpu-rig-llm/`** (`docs/operations/cluster-dns.md`, `docs/api/openai-compat.md`). Purpose-named stack deploy: **`./deploy.sh <stack|service>`** (`docs/operations/deploy-stacks.md`); lab site wiring via **`--profile gpu-rig`**.

## Build / test

- Python **3.10+**; main deps in **`requirements.txt`** (pins **`starlette<0.47`** for FastAPI compatibility with **`mcp`**).
- Tests: `pytest` (uses **`MODEL_TYPE=mock`**, **`IDEMPOTENCY_BACKEND=memory`**, **`JARVIS_OCR_ENGINE=mock`**, **`JARVIS_OCR_DETECTOR=mock`** via `tests/conftest.py`). Wand: `cd wand && pytest`. Tap collector: `cd tap_collector && pytest`.
- Pip cache directory: **`cache/build/pip`** (aligned with Docker).
- **Build / redeploy**: validation runs on Kubernetes, not Compose. Prefer **`./deploy.sh --profile gpu-rig <stack|service>`** (see **`docs/operations/deploy-stacks.md`**). Agents may also **`docker build` + `docker push`** to **`192.168.86.120:32000`** and run targeted **`kubectl apply -k`** / **`rollout restart`**. Destructive cluster ops stay off-limits unless explicitly requested (see **`.cursor/rules/build-and-redeploy.mdc`**).

## Conventions

- Keep **LLM** logic in root `api/`, `models/`, `utils/`; **ASR** only under `asr/`; **Wand** only under `wand/`; **Tap collector** only under `tap_collector/`; **Chat UI** only under `ui/`.
- When changing HTTP routes, **idempotency**, env vars, or Docker/K8s images: update **`docs/`** and **`mkdocs.yml`** in the same change; run **`mkdocs build --strict`** (matches **`Dockerfile.docs`**) so the docs site build does not break.
- When changing **system topology** or **cross-cutting flows** (routers, MCP modes, OCR pipeline, Compose/K8s service graph): update **`docs/architecture/`** per **`.cursor/rules/architecture-docs-sync.mdc`**.
- **OCR / vision MCP + image REST** (`docs/services/ocr.md`, `docs/mcp-payloads.md`, `jarvis_mcp/`): any change to **client-visible** MCP traffic or REST upload semantics must update **`## Spec revision history`** in the OCR spec and **`docs/mcp-payloads.md`**. Reflect breaking changes in the sibling **tiny-ocr** repo when applicable (see **`.cursor/rules/tiny-ocr-mcp-client-sync.mdc`** if present).
- Do not commit **`cache/`** contents (see **`.gitignore`**).

## Ollama

- Default (big) model id in K8s / Compose: **`llama3.1:8b`** on pinned **`ollama/ollama:0.4.7`** (Pascal / gpu-rig PoC).
- Fast tier: **`llama3.2:3b`** via **`metadata.model_tier: "fast"`**.
- **`gpt-oss:20b`** needs a newer Ollama than **0.4.7**; restore it after a verified image bump (see **`docs/operations/kubernetes.md`**).
- Compose exposes Ollama on **11434**; run **`ollama pull`** once per persistent volume / PVC.
