# Kubernetes

## Full stack (hybrid LLM + Redis)

The **`k8s/`** directory uses **Kustomize**. The root **`k8s/kustomization.yaml`** includes:

- **`redis/`** — Redis Deployment + ClusterIP (**6379**, service **`redis`**). Data on PVC **`redis-data`** (**5Gi**, **ReadWriteOnce**, AOF). Needs a **StorageClass** (default / hostpath / cloud block).
- **`ollama/`** — "big" tier Deployment on **3 GPUs**; models on PVC **`ollama-data`** (**30Gi**). Service **`ollama`**. Default model **`llama3.1:8b`** (see [Model pins](#model-pins-and-gpt-oss)).
- **`ollama-fast/`** — "fast" tier **StatefulSet** (**2** replicas × **1 GPU**); per-pod PVCs **`data-ollama-fast-0`**, **`data-ollama-fast-1`**. Service **`ollama-fast`**. Default model **`llama3.2:3b`**.
- **`llm-api/`** — LLM API (**8000**); ConfigMap **`jarvis-llm-api-config`** sets **`MODEL_TYPE=ollama`**, both Ollama URLs, and Redis-backed idempotency.

### Apply targets

Prefer purpose-named deploys: **`./deploy.sh <stack>`** (see [Deploy stacks](deploy-stacks.md)). Site PoC overlays below remain valid for manual `kubectl apply -k`.

| Overlay / path | When to use |
| -------------- | ----------- |
| **`k8s/overlays/tap-testing/`** | Portable MQTT + tap collector/stream (no lab LB/NFS). |
| **`k8s/overlays/core/`** | Portable Redis + Ollama + LLM API. |
| **`k8s/overlays/gpu-rig/`** | **Live PoC**: namespace **`jarvis-llm`**, fridge-prime NFS PVs, registry **`192.168.86.120:32000`**. |
| **`k8s/overlays/gpu-rig-ui/`** | Optional chat UI LoadBalancer (pinned **`192.168.86.6`** → **`ui.jarvis.lan`**). |
| **`k8s/overlays/gpu-rig-llm/`** | Optional llm-api LoadBalancer (pinned **`192.168.86.10`** → **`llm.jarvis.lan`**) + feedback/adapters NFS for OpenAI `/v1` + learning. |
| **`k8s/overlays/gpu-rig-health/`** | Oximeter collector in namespace **`health`** (pinned **`.12`** → **`oximeter.jarvis.lan`**) |
| **`k8s/overlays/gpu-rig-telemetry/`** | Optional Mosquitto + tap-collector (pinned **`.7` / `.8`** → **`mqtt` / `tap.jarvis.lan`**). |
| **`k8s/overlays/gpu-rig-dns/`** | Optional LAN CoreDNS for **`jarvis.lan`** (pinned **`192.168.86.9`**). See [Cluster DNS](cluster-dns.md). |
| **`k8s/overlays/microk8s/`** | Single-node MicroK8s with registry rewrite to **`localhost:32000`** (no NFS / namespace extras). |
| **`k8s/`** | Bare image names (`jarvis_llm_api`); cluster must already resolve them. |

```bash
# Purpose stacks (build + push + apply + rollout):
./deploy.sh tap-testing
./deploy.sh --profile gpu-rig tap-testing

# Verified gpu-rig + fridge-prime path (manual apply):
kubectl apply -k k8s/overlays/gpu-rig/

# Or a single component from the base tree:
kubectl apply -k k8s/redis
kubectl apply -k k8s/ollama
kubectl apply -k k8s/ollama-fast
kubectl apply -k k8s/llm-api
kubectl apply -k k8s/asr   # optional
kubectl apply -k k8s/wand  # optional interactive wand
kubectl apply -k k8s/overlays/gpu-rig-ui/         # optional chat UI (ui.jarvis.lan)
kubectl apply -k k8s/overlays/gpu-rig-llm/        # optional llm-api LAN LB (llm.jarvis.lan)
kubectl apply -k k8s/overlays/gpu-rig-telemetry/  # optional MQTT + tap collector
kubectl apply -k k8s/overlays/gpu-rig-dns/        # optional LAN DNS (jarvis.lan)
```

### Model pulls (once per PVC)

Models persist on the Ollama PVCs (NFS on gpu-rig). After the first successful pull they survive pod and node restarts.

```bash
kubectl -n jarvis-llm exec deploy/ollama -- ollama pull llama3.1:8b
kubectl -n jarvis-llm exec ollama-fast-0 -- ollama pull llama3.2:3b
kubectl -n jarvis-llm exec ollama-fast-1 -- ollama pull llama3.2:3b
```

(Omit **`-n jarvis-llm`** if you applied a root / microk8s overlay without that namespace.)

## Model pins and gpt-oss

| Tier | ConfigMap key | Current default | Notes |
|------|---------------|-----------------|-------|
| Big | **`MODEL_NAME`** | **`llama3.1:8b`** | Fits the pinned **`ollama/ollama:0.4.7`** image on Pascal. |
| Fast | **`MODEL_NAME_FAST`** | **`llama3.2:3b`** | One copy per fast StatefulSet pod. |

**`gpt-oss:20b`** is the longer-term big-tier target, but pulling it against **`0.4.7`** fails with HTTP **412** (newer Ollama required). After bumping the Ollama image tag in **`k8s/ollama`**, **`k8s/ollama-fast`**, and Compose — and re-validating on GTX 1070 — set **`MODEL_NAME`** back to **`gpt-oss:20b`** and pull once onto **`ollama-data`**.

## gpu-rig overlay (fridge NFS)

The **`k8s/overlays/gpu-rig/`** overlay adds:

- Namespace **`jarvis-llm`**
- StorageClass **`nfs-storage`** (static / no-provisioner)
- Static PVs on **fridge-prime** (`192.168.86.221:/media/kad/data/k8s/jarvis-llm/...`)
- PVC bind for **`ollama-data`** + NFS **`storageClassName`** on fast volumeClaimTemplates
- Image rewrite: **`jarvis_llm_api` → `192.168.86.120:32000/jarvis_llm_api:latest`**

Redis stays on the cluster default StorageClass (typically **`microk8s-hostpath`** on the GPU node). Do **not** use the MicroK8s addon StorageClass named **`nfs`** for model data — that provisioner lands on gpu-rig local disk, not fridge exports.

Build / push the API image (operator):

```bash
cd /media/kad/data/repositories/jarvis
DOCKER_BUILDKIT=1 docker build -t 192.168.86.120:32000/jarvis_llm_api:latest -f Dockerfile .
docker push 192.168.86.120:32000/jarvis_llm_api:latest
kubectl -n jarvis-llm rollout restart deploy/llm-api
```

Operational inventory, device-plugin notes, and reboot recovery live alongside the cluster in the sibling **`ai-sandbox/gpu-rig-k8s/`** tree (runbook / issues). Manifests for Jarvis itself are maintained **here**.

## Tier routing

Clients select the fast tier per request:

```http
POST /api/v1/events
Content-Type: application/json
Idempotency-Key: <client-key>

{ "event_type": "text_query",
  "content": {"text": "..."},
  "metadata": {"model_tier": "fast"} }
```

If `model_tier` is omitted (or the fast tier is not configured), the default tier handles the request. The response envelope reports the chosen tier under `response.meta.tier`.

## LLM API rolling updates and replicas

The **`llm-api`** Deployment uses **`RollingUpdate`** with **`maxUnavailable: 0`** so a replacement pod must pass **`readinessProbe`** (`GET /health`) before the previous pod terminates — safer for a single replica.

- **`replicas: 2`** is reasonable only when **`IDEMPOTENCY_BACKEND=redis`** (default in the bundled ConfigMap) and your Ollama tier has capacity. **`GET /api/v1/events/{id}`** status is still **per-process memory** (not shared across replicas); see [LLM events API](../api/llm-events.md).

**`PodDisruptionBudget` `llm-api`** sets **`minAvailable: 1`**. With **`replicas: 1`**, voluntary eviction (for example **`kubectl drain`**) can block until you temporarily delete the PDB or scale up. Remove **`pdb.yaml`** from **`k8s/llm-api/kustomization.yaml`** if you prefer not to enforce this on single-replica clusters.

## ConfigMap hot-reload (llm-api)

The bundled **`llm-api`** manifest mounts the same **`jarvis-llm-api-config`** ConfigMap twice:

1. **`envFrom`** — initial process environment at container start.
2. **Read-only volume** at **`/etc/jarvis/config`** — kubelet refreshes these files when the ConfigMap changes (typical delay on the order of tens of seconds).

The app sets **`JARVIS_CONFIG_DIR=/etc/jarvis/config`** and **`JARVIS_CONFIG_RELOAD_INTERVAL_S`** (default **30**). A background task **polls** file mtimes; when a key file changes, it updates **`os.environ`**, clears the cached **`Settings`**, and rebuilds **`LLMHandler`**, the idempotency store, **`ImageStore`**, and the OCR recognize semaphore (semaphore is replaced only when **`JARVIS_OCR_MAX_CONCURRENT`** changes).

**Caveats:** In-flight requests may finish on an older **`LLMHandler`** until they return; raising **`JARVIS_OCR_MAX_CONCURRENT`** while load is high can briefly behave oddly because the semaphore instance is replaced. **`LOG_LEVEL`** / **`LOG_JSON`** changes re-run log configuration. Compose / bare-metal can enable the same behavior by setting **`JARVIS_CONFIG_DIR`** to a directory of env-named files (or omit it to disable polling).

## GPU scheduling on a single multi-GPU node

The default manifests assume **5 GPUs** on the node, split as:

| Pod | `nvidia.com/gpu` request/limit | Replicas | Effective GPUs |
|-----|--------------------------------|----------|----------------|
| `ollama` (big) | 3 | 1 | 3 |
| `ollama-fast` | 1 | 2 | 2 |
| **Total** | | | **5** |

To free a GPU for ASR or experiments, either:

- drop `ollama` to **`nvidia.com/gpu: 2`**, or
- scale `ollama-fast` to **`replicas: 1`**.

## NVIDIA on Kubernetes prerequisites

- Recent NVIDIA datacenter / desktop driver (on the gpu-rig PoC: **580.x**; R535 / R550 LTS also documented for Pascal).
- **NVIDIA Container Toolkit** on each GPU node.
- **NVIDIA device-plugin** DaemonSet (or GPU operator). Pin plugin pods to GPU nodes (`gpu.present=true`) when the cluster has CPU-only workers.
- Node should advertise **`nvidia.com/gpu`** capacity matching the physical card count.

On **MicroK8s**, `microk8s enable gpu` or a manual device-plugin + **`RuntimeClass` `nvidia`** both work; the PoC uses a hostPath of **`/var/lib/kubelet/device-plugins`**.

## Pinned versions on Pascal (e.g. GTX 1070)

For long-term support on Pascal, pin **both image tags and host drivers** together:

| Layer | Suggested pin | Why |
|-------|--------------|-----|
| NVIDIA driver | **580.x** on Ubuntu 26.04 PoC (or **R535 / R550 LTS**) | Pascal (CC 6.1) still supported on these branches. |
| NVIDIA Container Toolkit | latest **1.14.x**+ | Tracks the driver cleanly. |
| NVIDIA k8s device-plugin | a tagged **`v0.14.x` / `v0.15.x`** release | Avoid `latest`. |
| Ollama image | **`ollama/ollama:0.4.7`** (this repo’s pin) | Verified to load on Pascal with `OLLAMA_FLASH_ATTENTION=0`. Bump only after testing (required for gpt-oss). |
| Model quantizations | **Q4_K_M / Q5_K_M** GGUF where applicable | Native FP16 is slow on Pascal; integer GGUFs perform better. |

The Pascal-specific Ollama env (already set in both manifests):

```yaml
env:
  - { name: OLLAMA_FLASH_ATTENTION, value: "0" }
  - { name: OLLAMA_KEEP_ALIVE,      value: "24h" }
  - { name: OLLAMA_NUM_PARALLEL,    value: "1" }  # big tier; fast tier uses "2"
  - { name: OLLAMA_MAX_LOADED_MODELS, value: "1" }
```

## Optional overlays (not in root kustomization)

- **`k8s/asr/`**, **`k8s/storage/`**, **`k8s/docs/`**, **`k8s/tts/`**, **`k8s/wand/`**, **`k8s/ui/`**, **`k8s/mosquitto/`**, **`k8s/tap-collector/`** — apply when needed.
- **`k8s/base/`** — namespace **`jarvis-llm`** (merge into your env overlay if you use namespaces).
- **`k8s/observability/`** — README + commented Prometheus samples.

Chat UI is **optional** and not part of the gpu-rig core overlay (keeps PoC RAM free). Apply separately with the registry rewrite:

```bash
# Image already pushed as 192.168.86.120:32000/jarvis_ui:latest
kubectl apply -k k8s/overlays/gpu-rig-ui/
# MetalLB assigns a LAN IP (e.g. http://192.168.86.3:8003/)
kubectl -n jarvis-llm get svc ui
```

MQTT telemetry (**Mosquitto** + **tap-collector**) is also optional:

```bash
kubectl apply -k k8s/overlays/gpu-rig-telemetry/
kubectl -n jarvis-llm get svc mosquitto tap-collector
# Pi: TAP_MQTT_HOST=mqtt.jarvis.lan TAP_MQTT_PORT=1883
# UI: http://tap.jarvis.lan/ui/
```

**`k8s/asr`**, **`k8s/tts`**, **`k8s/wand`**, and **`k8s/ui`** ship ConfigMaps with **`envFrom`** plus a projected volume for [ConfigMap hot-reload](kubernetes-config-hot-reload.md).

## Images and the local registry

Deployments reference the **`jarvis_llm_api`**, **`jarvis_asr_service`**, and optional **`jarvis_docs`** / **`jarvis_tts`** image names with `imagePullPolicy: IfNotPresent`. They are not on Docker Hub — you must build and push them into a registry the cluster can reach. See [Local container registry](registry.md).

Quick path for MicroK8s (operator runs):

```bash
microk8s enable registry
export REGISTRY=localhost:32000   # or 192.168.86.120:32000 from fridge-prime
./build.sh --registry "$REGISTRY" --push --build-optional
kubectl apply -k k8s/overlays/gpu-rig/    # PoC
# or: kubectl apply -k k8s/overlays/microk8s/
```

## Redis

Shared **`Idempotency-Key`** state: see [Redis for idempotency](redis.md).

## Migrating from the old ollama-fast Deployment

If a cluster still has **`Deployment/ollama-fast`**, delete it before or when applying the StatefulSet (same labels; both cannot own the pods):

```bash
kubectl -n jarvis-llm delete deploy ollama-fast --ignore-not-found
kubectl apply -k k8s/overlays/gpu-rig/
```
