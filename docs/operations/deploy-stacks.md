# Deploy stacks

Purpose-named Kubernetes deploy via **`./deploy.sh`**. Stacks describe *what* to build and apply (e.g. tap-testing). Registry hosts, MetalLB, and NFS stay in **config** and **profiles**, not in the stack catalog.

Local Compose remains **`docker compose`** / **`./jarvis.sh`** start/stop — this path targets a cluster with a reachable registry (see [Local container registry](registry.md)).

## Quick start

```bash
cp deploy/config.example.env deploy.env   # optional local overrides
# edit REGISTRY=… to match your cluster

./deploy.sh --list
./deploy.sh tap-testing                   # portable overlay + build/push/apply/rollout
./deploy.sh tap-collector                 # one service image + restart that deploy
./deploy.sh --profile gpu-rig tap-testing # lab overlay (NFS / MetalLB)
./deploy.sh --profile microk8s core
```

Flags: **`--apply-only`**, **`--build-only`**, **`--profile NAME`**, **`--list`**.

## Layers

| Layer | Path | Role |
| ----- | ---- | ---- |
| Stack catalog | `deploy/stacks/*.env` | Purpose, Dockerfiles, deployment names, default portable overlay |
| Global config | `deploy.env` (gitignored) / `deploy/config.example.env` | `REGISTRY`, `IMAGE_TAG`, `NAMESPACE`, `KUBECTL` |
| Profile | `deploy/profiles/*.env` | Site overlay path overrides; optional kubeconfig env file |
| Overlay | `k8s/overlays/…` | Cluster wiring (LB, PVC, NFS) |

Stack names use hyphens (`tap-testing`). Profile overlay overrides use underscores (`STACK_OVERLAY_tap_testing`).

## Portable vs site overlays

| Stack CLI | Default (portable) overlay | Example site profile (`gpu-rig`) |
| --------- | -------------------------- | -------------------------------- |
| `tap-testing` (alias `telemetry`) | `k8s/overlays/tap-testing` | `k8s/overlays/gpu-rig-telemetry` |
| `core` (alias `llm`) | `k8s/overlays/core` | `k8s/overlays/gpu-rig` |
| `ui` | `k8s/overlays/ui` | `k8s/overlays/gpu-rig-ui` |
| `health` (alias `oximeter`) | `k8s/overlays/health` | `k8s/overlays/gpu-rig-health` |
| `dns` | `k8s/overlays/dns` | `k8s/overlays/gpu-rig-dns` |

Services under a stack (e.g. `tap-collector`, `tap-stream`, `telemetry-bridge`, `llm-api`) build **one** image, apply the stack overlay (rewriting all sibling jarvis images so bare names are not re-applied), and restart **that** deployment only.

At apply time, `deploy.sh` builds a temporary Kustomize wrapper that sets `images:` from `REGISTRY` / `IMAGE_TAG`. Portable overlays intentionally omit lab registry hosts.

## Custom site

1. Copy or author an overlay under `k8s/overlays/<your-site>/` (see [Kubernetes](kubernetes.md); repo notes in `k8s/overlays/README.md`).
2. Point the stack at it:

```bash
# deploy.env or profile
STACK_OVERLAY_tap_testing=k8s/overlays/my-site/telemetry
REGISTRY=registry.example.com/team
```

3. Run `./deploy.sh tap-testing`.

## tap-testing note

The **tap-testing** stack is the Jarvis-side MQTT ingest (Mosquitto, tap-collector, tap-stream, Redpanda, telemetry-bridge). The sibling **tap-testing** Pi/SBC publisher repo is separate and is not deployed by this script.

## Related

- [Kubernetes](kubernetes.md) — overlay map and gpu-rig PoC details
- [Local container registry](registry.md) — registry prerequisites
- [Tap collector](../services/tap-collector.md) / [Tap stream](../services/tap-stream.md)
