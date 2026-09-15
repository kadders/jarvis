# Kubernetes ConfigMap hot-reload (default pattern)

Jarvis **Python / FastAPI** services that ship with Kubernetes manifests use the same **default** configuration pattern:

1. **`envFrom`** — inject ConfigMap keys as environment variables at container start.
2. **Read-only volume** — mount the **same** ConfigMap at **`/etc/jarvis/config`** so the kubelet can refresh files when the ConfigMap changes (without restarting the pod).
3. **`JARVIS_CONFIG_DIR=/etc/jarvis/config`** and **`JARVIS_CONFIG_RELOAD_INTERVAL_S`** (seconds, minimum **5** in code) — enable a background task that **polls** file mtimes, updates **`os.environ`**, clears the cached settings object, and **rebinds** service-specific singletons (LLM handler, ASR handler, TTS flags, etc.).

Shared implementation (repository root): **`utils/k8s_config_reload.py`** (`scan_config_dir`, `run_mounted_config_poll_loop`).

Because **ASR**, **TTS**, and **Chat UI** Docker images build from **subdirectories** (`asr/`, `tts/`, `ui/`), they each carry a **copy** of that module:

- **`asr/utils/k8s_config_reload.py`**
- **`tts/k8s_config_reload.py`**
- **`ui/k8s_config_reload.py`**

When you change the polling contract or scan rules, **update all copies** in one change (or refactor the build to a single shared image layer).

## Services using this pattern

| Service | ConfigMap | Mount path | Rebind behavior |
| ------- | --------- | ---------- | --------------- |
| **LLM API** | `jarvis-llm-api-config` | `/etc/jarvis/config` | Recreates `LLMHandler`, idempotency store, `ImageStore`, OCR semaphore when limits change; optional **`configure_logging()`** when **`LOG_LEVEL`** / **`LOG_JSON`** files change. |
| **ASR** | `jarvis-asr-config` | `/etc/jarvis/config` | Stops directory monitor, recreates **`ASRHandler`**, restarts monitor when **`MONITOR_AUDIO_DIR`** is on. |
| **TTS** | `jarvis-tts-config` | `/etc/jarvis/config` | Refreshes in-process **`Settings`** (e.g. **`TTS_ENABLE_MOCK`**). |
| **Chat UI** | `jarvis-ui-config` | `/etc/jarvis/config` | Refreshes in-process **`Settings`** (LLM base URL, timeouts). |
| **Wand** | `jarvis-wand-config` | `/etc/jarvis/config` | Refreshes wand settings when ConfigMap files change. |

## Non-Python or static containers

**Ollama**, **Redis**, **nginx (docs)**, and similar workloads **do not** use this in-process reload. For them, use **rolling restarts** (e.g. Stakater Reloader, Argo CD sync, or `kubectl rollout restart`) when ConfigMaps change.

## New containers (convention)

When adding a **new FastAPI (or Python) service** to this repository:

1. Add a **ConfigMap** with the env keys the app reads.
2. In the **Deployment**: `envFrom` + **volumeMount** at **`/etc/jarvis/config`** + **`volumes.configMap`** (same name as `envFrom`).
3. Set **`JARVIS_CONFIG_DIR`** and **`JARVIS_CONFIG_RELOAD_INTERVAL_S`** in the ConfigMap (or static env).
4. Copy **`utils/k8s_config_reload.py`** into the image if the Docker **context** is not the repo root; otherwise import **`utils.k8s_config_reload`**.
5. On startup: attach **`app.state.config_reload_lock = asyncio.Lock()`**, run **`run_mounted_config_poll_loop(...)`** when **`JARVIS_CONFIG_DIR`** is set, and implement **`on_rebind`** to rebuild any cached clients or handlers.

See also [Kubernetes operations](kubernetes.md) and [Environment variables](../reference/environment-variables.md).
