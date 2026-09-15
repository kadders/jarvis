# Local container registry

The Kubernetes Deployments in `k8s/` reference local image names (`jarvis_llm_api`, `jarvis_asr_service`, `jarvis_docs`, `jarvis_tts`) that are **not** on a public registry. You must run a registry the cluster can pull from, push the built images into it, and apply a Kustomize overlay that rewrites image names to that registry.

This is a **prerequisite** for every Kubernetes deployment path. Pick one of the options below before running `kubectl apply -k …`.

## Option A — MicroK8s built-in registry (recommended for the MicroK8s path)

MicroK8s ships a registry addon that runs in-cluster, listens on `localhost:32000` on the host, and is auto-trusted by the cluster's containerd (no TLS / `insecure-registries` workarounds needed on the node).

```bash
# Operator runs these
microk8s enable registry
microk8s status --wait-ready
curl -s http://localhost:32000/v2/_catalog          # sanity check
```

Build and push:

```bash
export REGISTRY=localhost:32000
./build.sh --registry "$REGISTRY" --push --build-optional
# or purpose stacks: ./deploy.sh --profile microk8s core
```

Apply the overlay:

```bash
kubectl apply -k k8s/overlays/gpu-rig/      # PoC: 192.168.86.120:32000 + jarvis-llm + NFS
# or:
kubectl apply -k k8s/overlays/microk8s/    # localhost:32000 rewrite only
```

The gpu-rig overlay rewrites **`jarvis_llm_api`** to **`192.168.86.120:32000/jarvis_llm_api:latest`**. The microk8s overlay rewrites:

| Bare image name (`k8s/`) | Becomes |
|--------------------------|---------|
| `jarvis_llm_api`         | `localhost:32000/jarvis_llm_api:latest` |
| `jarvis_asr_service`     | `localhost:32000/jarvis_asr_service:latest` |
| `jarvis_docs`            | `localhost:32000/jarvis_docs:latest` |
| `jarvis_tts`             | `localhost:32000/jarvis_tts:latest` |
| `jarvis_ui`              | `localhost:32000/jarvis_ui:latest` |

Public images (`ollama/ollama:0.4.7`, `redis:7-alpine`) come straight from Docker Hub and are unaffected.

## Option B — Standalone Docker registry (kind / k3d / generic)

```bash
docker run -d --restart=always -p 5000:5000 --name jarvis-registry registry:2
```

Then make the cluster trust it:

| Cluster | How to trust `host:5000` |
|---------|---------------------------|
| **kind** | `kind create cluster --config kind-config.yaml` with `containerdConfigPatches:` that adds the registry as a mirror. |
| **k3d** | `k3d cluster create --registry-config registries.yaml` listing the registry. |
| **plain Docker / Compose** | Edit `/etc/docker/daemon.json` on each node: `"insecure-registries": ["host:5000"]`, then restart the Docker daemon. |

Build, tag, push:

```bash
export REGISTRY=<host-ip-or-hostname>:5000
./build.sh --registry "$REGISTRY" --push --build-optional
```

Copy `k8s/overlays/microk8s/` to `k8s/overlays/<your-name>/` and replace `localhost:32000` with `$REGISTRY`. Apply with `kubectl apply -k k8s/overlays/<your-name>/`.

## Option C — External / authenticated registry

For Harbor, GitLab, GHCR, ECR, GCR, ACR, etc.:

```bash
docker login <registry>
export REGISTRY=registry.example.com/team
./build.sh --registry "$REGISTRY" --push --build-optional --tag v1.2.3
```

Private registries need an `imagePullSecret`:

```bash
kubectl create secret docker-registry jarvis-pull \
  --docker-server=registry.example.com \
  --docker-username=... --docker-password=... --docker-email=...
```

Reference the secret from each Deployment (or add a Kustomize patch in your overlay).

## `build.sh` flags relevant to the registry

| Flag | Env var | Purpose |
|------|---------|---------|
| `--registry HOST[:PORT][/NAMESPACE]` | `REGISTRY` | Image prefix; trailing slash is trimmed. |
| `--tag TAG` | `IMAGE_TAG` | Tag to apply to retagged images (default `latest`). |
| `--push` | | Push tagged images. Errors out if `--registry` is missing. |
| `--no-push` | | Tag but do not push. |
| `--build-optional` | | Also build `jarvis_docs` (`Dockerfile.docs`) and `jarvis_tts` (`tts/Dockerfile`). |

Shorthand for retagging an existing build:

```bash
./jarvis.sh push-images --registry localhost:32000 --build-optional
```

## Sanity check

After pushing, list the registry:

```bash
curl -s http://localhost:32000/v2/_catalog | jq .       # MicroK8s addon
curl -s http://localhost:5000/v2/_catalog | jq .        # standalone registry
```

You should see the four `jarvis_*` repositories. If not, `./build.sh` either skipped the build or failed to push — re-run with `--push` and check the output for errors.

## fridge-prime worker nodes (gpu-rig cluster)

The **MicroK8s registry addon** runs on **gpu-rig** (`192.168.86.120:32000`, HTTP). **fridge-prime** (`192.168.86.221`) is an NFS + worker node in the same cluster.

| Runtime | fridge-prime setup |
| ------- | ------------------ |
| **Docker** | Add `"insecure-registries": ["192.168.86.120:32000"]` to `/etc/docker/daemon.json`, restart Docker. Then `docker pull 192.168.86.120:32000/jarvis_ui:latest` works for Compose / manual runs. |
| **MicroK8s / containerd** | Workers only trust `localhost:32000` by default. Add a **hosts.toml** entry so kubelet can pull `192.168.86.120:32000/...` images when pods schedule on fridge-prime. |

One-time on **fridge-prime** (or any worker):

```bash
# From the Jarvis repo on fridge-prime:
sudo ./scripts/configure-microk8s-registry-worker.sh

# Or manually:
sudo mkdir -p /var/snap/microk8s/current/args/certs.d/192.168.86.120:32000
sudo tee /var/snap/microk8s/current/args/certs.d/192.168.86.120:32000/hosts.toml <<'EOF'
server = "http://192.168.86.120:32000"

[host."http://192.168.86.120:32000"]
  capabilities = ["pull", "resolve"]
EOF
sudo chown root:microk8s /var/snap/microk8s/current/args/certs.d/192.168.86.120:32000/hosts.toml
sudo snap restart microk8s
```

Verify Kubernetes pull on fridge-prime:

```bash
kubectl run registry-pull-test --rm -it --restart=Never \
  --image=192.168.86.120:32000/jarvis_ui:latest \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"fridge-prime"}}}' \
  -n jarvis-llm --command -- echo ok
```

Workers that trust the LAN registry can schedule anywhere in the cluster. **Hostname `nodeSelector` patches** that pinned llm-api, ui, tap-collector, tap-stream, telemetry-bridge, and cluster-dns to gpu-rig were removed once fridge-prime could pull from `192.168.86.120:32000`. **GPU workloads** (Ollama, ollama-fast) keep affinity via `nodeSelector: gpu.present: "true"` plus `nvidia.com/gpu` requests.

Build/push from **fridge-prime** (same as gpu-rig):

```bash
export REGISTRY=192.168.86.120:32000
./build.sh --registry "$REGISTRY" --push --build-optional
```

## Why not just `docker save | microk8s ctr image import`?

`ctr image import` works for a single node, but it is **not** what the Deployment's `imagePullPolicy: IfNotPresent` is designed for: it stores the image directly in containerd's content store under the local name, and new nodes / restarts won't have it. A real registry — even an in-cluster one — is the canonical and portable way, and the only path that scales beyond a single MicroK8s host.
