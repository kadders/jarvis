# Jarvis LLM stack

Microservices for an event-driven **LLM API** (FastAPI), **ASR**, **Wand** (interactive gesture + incantation), optional **TTS**, **Redis** idempotency, and **Ollama** (default big tier **`llama3.1:8b`** on pinned **`0.4.7`**; fast **`llama3.2:3b`**). Operator documentation is built with **MkDocs**; a **Model Context Protocol** bridge lives under **`jarvis_mcp/`**.

## Requirements

| Context | Notes |
| ------- | ----- |
| **Python** | 3.10+ for LLM API, ASR, MCP, docs build |
| **Docker** | Recommended for full stack (LLM + ASR + Redis + Ollama) |
| **GPU** | Optional; speeds HF ASR/LLM and Ollama |

---

## Run the full stack (Docker Compose)

From the repository root. Creates **`redis`**, **`ollama`**, **`llm_api`** (8000), **`asr_service`** (8001), **`wand_service`** (8002), **`ui_service`** (8003). Uses **`.env`** when present; compose also sets Ollama + Redis idempotency env for **`llm_api`**.

```bash
docker compose build
docker compose up -d
docker compose exec ollama ollama pull llama3.1:8b
docker compose --profile multi-llm exec ollama_fast ollama pull llama3.2:3b
curl -s http://localhost:8000/health
curl -s http://localhost:8001/health
```

**Ollama on the host:** Compose maps **`11434:11434`**, so you can run `ollama` CLI against `localhost:11434` if needed. **`gpt-oss:20b`** needs a newer Ollama than the pinned **`0.4.7`** image.

**Stop:**

```bash
docker compose down
```

Some older helper scripts call **`docker-compose`** (hyphen). If you use those, install Docker Compose V1 or symlink **`docker-compose`** to the **`docker compose`** plugin.

---

## Run only part of the stack (Compose)

Start named services (examples):

```bash
docker compose up -d redis ollama llm_api
docker compose up -d asr_service
```

Use a **`.env`** file or `export` to change **`MODEL_TYPE`** (e.g. `mock` or `huggingface`) without editing **`docker-compose.yml`**.

---

## Run the LLM API on the host (Python / uvicorn)

Useful for quick iteration without rebuilding images.

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Fast local / CI-style behavior (no Ollama, no Redis required)
export MODEL_TYPE=mock
export IDEMPOTENCY_BACKEND=memory
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

**With Ollama on the same machine** (Ollama listens on 11434):

```bash
export MODEL_TYPE=ollama
export MODEL_NAME=llama3.1:8b
export OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
export IDEMPOTENCY_BACKEND=memory   # or redis + REDIS_URL if Redis is running
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

**With Redis for idempotency** (e.g. local `docker run redis` or Compose **`redis`** only):

```bash
export IDEMPOTENCY_BACKEND=redis
export REDIS_URL=redis://127.0.0.1:6379/0
```

**CLI shortcut** (LLM API only; activates `venv` if present):

```bash
./jarvis.sh start
./jarvis.sh start --container    # delegates to deploy.sh / compose
```

---

## Run the ASR service on the host

From **`asr/`** (paths and imports assume that working directory):

```bash
cd asr
python3 -m venv ../venv && source ../venv/bin/activate   # or use a dedicated venv
pip install -r requirements.txt
uvicorn api.main:app --reload --host 0.0.0.0 --port 8001
```

Health: **`GET http://localhost:8001/health`**.

---

## Run the TTS stub (optional)

From **`tts/`**:

```bash
cd tts
pip install fastapi uvicorn pydantic
uvicorn main:app --host 0.0.0.0 --port 8002
```

Or build/run the small image (**`tts/Dockerfile`**) and/or apply **`k8s/tts/`** if you use Kubernetes.

---

## Run the MCP bridge (stdio)

Exposes tools that call the LLM HTTP API (and optionally ASR). From the **repository root**:

```bash
source venv/bin/activate
pip install -r requirements.txt
export JARVIS_LLM_BASE_URL=http://127.0.0.1:8000/api/v1
# Optional:
# export JARVIS_ASR_BASE_URL=http://127.0.0.1:8001/api/v1
python -m jarvis_mcp
```

Configure your MCP host to launch this command. See **`docs/mcp.md`** for tool names and env vars (**`JARVIS_LLM_TIMEOUT_S`**, **`JARVIS_LLM_MAX_RETRIES`**, etc.).

---

## Run the documentation site (MkDocs)

**Locally:**

```bash
pip install -r requirements-docs.txt
mkdocs serve          # http://127.0.0.1:8000 — stop LLM API first if it uses 8000, or use --dev-addr
mkdocs build          # static output in site/
```

**Docker:**

```bash
docker compose -f docker-compose.docs.yml up --build
# default mapping: http://localhost:8080
```

**Kubernetes:** build/push **`jarvis_docs:latest`** per **`Dockerfile.docs`**, then **`kubectl apply -k k8s/docs`** (see **`k8s/docs/README.md`**).

---

## (Prerequisite) Local container registry

Every Kubernetes path below needs a reachable container registry so the cluster can pull **`jarvis_llm_api`**, **`jarvis_asr_service`**, and the optional **`jarvis_docs`** / **`jarvis_tts`** images. Pick **one**:

### Option A — MicroK8s built-in registry (recommended for the microk8s path)

Runs in-cluster on **`localhost:32000`** of the host. MicroK8s' containerd is preconfigured to trust it without TLS.

```bash
microk8s enable registry              # operator runs this; agent will not
microk8s status --wait-ready
curl -s http://localhost:32000/v2/_catalog   # should respond with JSON
```

To use it from `build.sh` and Kustomize:

```bash
export REGISTRY=localhost:32000
./build.sh --registry "$REGISTRY" --push --build-optional
kubectl apply -k k8s/overlays/microk8s/
```

`k8s/overlays/microk8s/kustomization.yaml` rewrites every `jarvis_*` image to `localhost:32000/jarvis_*:latest`.

### Option B — Standalone Docker registry (kind / k3d / plain Docker)

For non-MicroK8s clusters, run a registry container and configure your cluster to trust it.

```bash
docker run -d --restart=always -p 5000:5000 --name jarvis-registry registry:2
# Make sure the cluster network can reach <host-ip>:5000 (firewall, /etc/docker/daemon.json
# `insecure-registries`, kind extraMounts, k3d --registry-config, etc.).
```

Then build and push:

```bash
export REGISTRY=<host-ip-or-hostname>:5000
./build.sh --registry "$REGISTRY" --push --build-optional
```

Copy `k8s/overlays/microk8s/` to `k8s/overlays/<your-name>/`, replace `localhost:32000` with `$REGISTRY`, and `kubectl apply -k k8s/overlays/<your-name>/`.

### Option C — Your own external registry (Harbor, GitLab, GHCR, ECR, GCR, …)

Authenticate (`docker login <registry>`), then run the same build.sh flow with `REGISTRY` set to your namespace prefix (e.g. `registry.example.com/team`). Provide `imagePullSecrets` to the cluster if the registry is private; see **`k8s/overlays/README.md`**.

> **Reminder**: The agent does not run cluster commands — every `kubectl`, `microk8s`, or `docker run registry` line in this README is something the **operator** runs.

### Build script flags

| Flag | Purpose |
| ---- | ------- |
| `--registry HOST[:PORT]` / `REGISTRY=` | Tag built images with this prefix (e.g. `localhost:32000`). |
| `--tag TAG` / `IMAGE_TAG=` | Tag to apply (default `latest`). |
| `--push` | Push tagged images. Requires `--registry`. |
| `--no-push` | Tag locally only. |
| `--build-optional` | Also build `jarvis_docs` (from `Dockerfile.docs`) and `jarvis_tts` (from `tts/Dockerfile`). |

Shorthand wrapper for retagging an existing build:

```bash
./jarvis.sh push-images --registry localhost:32000 --build-optional
```

---

## Run on Kubernetes

Core bundle (Redis + Ollama **big** tier + Ollama **fast** tier + LLM API):

```bash
# After: registry is up AND ./build.sh --registry $REGISTRY --push has run
kubectl apply -k k8s/overlays/gpu-rig/           # verified PoC (jarvis-llm + fridge NFS)
# or kubectl apply -k k8s/overlays/microk8s/     # localhost:32000 rewrite only
# or kubectl apply -k k8s/                       # bare jarvis_* names
```

The default manifests are a **hybrid LLM** deployment:

- **`k8s/ollama/`** — **`llama3.1:8b`**, **3 GPUs**, PVC **`ollama-data`**. Service **`ollama`**.
- **`k8s/ollama-fast/`** — **StatefulSet** ×2, **`llama3.2:3b`**, **1 GPU** each, per-pod PVCs. Service **`ollama-fast`**.
- **`k8s/llm-api/`** — reads both `OLLAMA_BASE_URL` and `OLLAMA_FAST_BASE_URL`.

**Optional** components (apply when you need them):

```bash
kubectl apply -k k8s/asr
kubectl apply -k k8s/storage
kubectl apply -k k8s/docs
kubectl apply -k k8s/tts
kubectl apply -k k8s/base          # namespace jarvis-llm
```

After pods are up, pull the models once into each Ollama PVC:

```bash
kubectl -n jarvis-llm exec deploy/ollama -- ollama pull llama3.1:8b
kubectl -n jarvis-llm exec ollama-fast-0 -- ollama pull llama3.2:3b
kubectl -n jarvis-llm exec ollama-fast-1 -- ollama pull llama3.2:3b
```

Details: **`docs/operations/kubernetes.md`**, **`docs/operations/redis.md`**.

---

## Ubuntu 24.04 / 26.04 with MicroK8s (multi-GPU host)

This section is the **end-to-end install path** for a single Ubuntu LTS box that will host the whole stack on **MicroK8s** with multiple NVIDIA GPUs (verified target: **5x GeForce GTX 1070 8 GiB**, Pascal / CC 6.1).

> Replace **`<USER>`** with your login. Run commands as a normal user; **`sudo`** is shown explicitly where needed.

### 1. Base OS prep (24.04 / 26.04)

```bash
sudo apt update && sudo apt -y full-upgrade
sudo apt -y install curl jq git build-essential snapd
sudo systemctl enable --now snapd.socket
```

If you are on Ubuntu **26.04** before NVIDIA ships matching DKMS packages, prefer **24.04 LTS** for the GPU node until R535/R550 LTS drivers land for the new kernel.

### 2. NVIDIA driver (Pascal-friendly, pinned LTS branch)

```bash
ubuntu-drivers devices                 # check that GTX 1070 / nvidia-driver-535 is offered
sudo apt -y install nvidia-driver-535   # or nvidia-driver-550 once it is the LTS on your release
sudo reboot
```

After reboot:

```bash
nvidia-smi                              # should list all 5 GPUs
```

**Why R535 LTS:** Pascal (compute 6.1) is still supported on the **R535 / R550** branches. Avoid bumping to drivers that drop Pascal until you have re-tested.

### 3. NVIDIA Container Toolkit (so containers can see GPUs)

```bash
distribution=$(. /etc/os-release;echo $ID${VERSION_ID})
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update
sudo apt -y install nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 4. MicroK8s

```bash
sudo snap install microk8s --classic --channel=1.30/stable
sudo usermod -a -G microk8s <USER>
mkdir -p ~/.kube && sudo chown -R <USER> ~/.kube
newgrp microk8s     # or log out / back in
microk8s status --wait-ready
microk8s enable dns hostpath-storage
```

A `kubectl` alias makes the rest of the README work as-is:

```bash
sudo snap alias microk8s.kubectl kubectl
```

### 5. GPU support inside MicroK8s

Use the built-in GPU addon — it installs the NVIDIA **device-plugin** and configures the cluster:

```bash
microk8s enable gpu

# Wait for the DaemonSet to roll out and the node to advertise GPUs
kubectl -n gpu-operator-resources rollout status ds/nvidia-device-plugin-daemonset || true
kubectl describe node | grep -i nvidia.com/gpu
```

The describe output should show `nvidia.com/gpu: 5` (Capacity / Allocatable) for this host.

### 6. Enable the in-cluster container registry

MicroK8s ships a registry addon that exposes `localhost:32000` on the host and is auto-trusted by the cluster's containerd. See **"(Prerequisite) Local container registry"** above; in short:

```bash
microk8s enable registry
curl -s http://localhost:32000/v2/_catalog        # sanity check
```

### 7. Build, tag, and push the jarvis images

From the repo root:

```bash
export REGISTRY=localhost:32000
./build.sh --registry "$REGISTRY" --push --build-optional
# Pushes:
#   localhost:32000/jarvis_llm_api:latest
#   localhost:32000/jarvis_asr_service:latest
#   localhost:32000/jarvis_docs:latest      (because --build-optional)
#   localhost:32000/jarvis_tts:latest       (because --build-optional)
```

If you have an existing local build, just retag and push:

```bash
./jarvis.sh push-images --registry localhost:32000 --build-optional
```

### 8. Apply the stack (with the microk8s overlay)

```bash
kubectl apply -k k8s/overlays/gpu-rig/    # preferred for gpu-rig + fridge-prime
# or: kubectl apply -k k8s/overlays/microk8s/
kubectl get pods -n jarvis-llm -w
```

Pull both Ollama models **once** onto the PVCs (fridge NFS in the gpu-rig overlay — survives reboot):

```bash
kubectl -n jarvis-llm exec deploy/ollama -- ollama pull llama3.1:8b
kubectl -n jarvis-llm exec ollama-fast-0 -- ollama pull llama3.2:3b
kubectl -n jarvis-llm exec ollama-fast-1 -- ollama pull llama3.2:3b
```

Smoke test through a port-forward:

```bash
kubectl -n jarvis-llm port-forward svc/llm-api 8000:8000 &
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/api/v1/events \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: smoke-1' \
  -d '{"event_type":"text_query","content":{"text":"hi"}}' | jq '.response.meta'
curl -s -X POST http://127.0.0.1:8000/api/v1/events \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: smoke-2' \
  -d '{"event_type":"text_query","content":{"text":"hi"},"metadata":{"model_tier":"fast"}}' \
  | jq '.response.meta'
```

The first response reports `"tier":"default"` (big tier, `llama3.1:8b`), the second `"tier":"fast"` (fast tier, `llama3.2:3b`).

### 9. GPU layout on this host

With 5 GPUs available, the default manifests reserve them as:

| Pod | `nvidia.com/gpu` | Model | Notes |
|-----|------------------|-------|-------|
| `ollama` (big) × 1 | **3** | `llama3.1:8b` | Deployment + PVC `ollama-data`. |
| `ollama-fast` × 2 | **1 each** (= 2 GPUs) | `llama3.2:3b` | StatefulSet; PVCs `data-ollama-fast-0/1`. |

That sums to **5 GPUs**. To free a GPU for ASR or experimentation, edit `k8s/ollama/deployment.yaml` (drop the big tier to 2 GPUs) or scale `ollama-fast` to **`replicas: 1`**.

### 10. Pinned versions (record what worked on Pascal)

After verifying the stack:

```bash
nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1   # NVIDIA driver
microk8s version
kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[*].image}{"\n"}{end}'
```

The repo currently pins **`ollama/ollama:0.4.7`** in `k8s/ollama/`, `k8s/ollama-fast/`, and `docker-compose.yml`. Bump those tags together (and re-test on Pascal) before changing.

### 11. Tear down

```bash
kubectl delete -k k8s/overlays/gpu-rig/
# or: kubectl delete -k k8s/overlays/microk8s/
microk8s disable registry          # optional: drop the in-cluster registry
microk8s stop                      # optional: stop without uninstalling
```

---

## Helper scripts (repository root)

| Script | Purpose |
| ------ | ------- |
| **`./jarvis.sh`** | Front door: **`help`**, **`build`**, **`push-images`**, **`deploy`**, **`dev-setup`**, **`test`**, **`health`**, **`start`** / **`start --container`**, **`stop`**, **`logs`**, **`asr-generate`**, **`update-requirements`** |
| **`./build.sh`** | Builds images; flags include **`--build-optional`**, **`--registry HOST[:PORT]`**, **`--tag TAG`**, **`--push`** / **`--no-push`**, **`--skip-tests`**, **`--skip-models`**, **`--no-prefetch-deps`** |
| **`./deploy.sh`** | **`--dev`/`--prod`**, **`-d`**, **`--llm-only`**, **`--asr-only`** |
| **`./prefetch_deps.sh`** | Download heavy wheels into **`cache/build/pip`** |
| **`./download_models.sh`** | Fetch model artifacts (when configured) |
| **`./run_tests.sh`** | **`--verbose`**, **`--coverage`**, **`--llm`**, **`--asr`**, **`--path`** |
| **`./setup_dev.sh`** | Dev env **`--llm`**, **`--asr`**, **`--all`** |
| **`./check_health.sh`** | Health checks against running services |

Example:

```bash
./jarvis.sh dev-setup
./jarvis.sh build --build-optional --registry localhost:32000 --push
./jarvis.sh deploy --dev --detached
./jarvis.sh test
./jarvis.sh health
```

---

## Try the LLM API

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/events \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: readme-demo" \
  -d '{"event_type":"text_query","content":{"text":"Hello"}}' | jq .
```

---

## Configuration and layout

- **Env vars:** **`docs/reference/environment-variables.md`**
- **Ollama / gpt-oss:** [Run gpt-oss locally with Ollama](https://developers.openai.com/cookbook/articles/gpt-oss/run-locally-ollama)
- **Caches:** **`cache/build/pip`**, **`cache/models/`**, **`cache/redis`**, **`cache/ollama`**, **`cache/scratch/`**, **`cache/pytest/`** (see **`.gitignore`**)

---

## Development quick check

```bash
source venv/bin/activate
export MODEL_TYPE=mock
export IDEMPOTENCY_BACKEND=memory
pytest
```

---

## Project layout (short)

| Path | Role |
| ---- | ---- |
| `api/`, `models/`, `utils/` | LLM FastAPI app, handler, idempotency |
| `asr/` | ASR FastAPI app |
| `jarvis_mcp/` | MCP stdio server |
| `tts/` | Optional TTS stub |
| `docs/` | MkDocs source |
| `k8s/` | Kubernetes Kustomize trees |

Contributor notes: **`AGENTS.md`**, **`.cursor/rules/`**.
