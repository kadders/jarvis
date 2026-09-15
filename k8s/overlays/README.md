# Kustomize overlays

Overlays layer environment-specific changes on top of `k8s/` bases. Prefer **`./deploy.sh <stack>`** for build/push/apply (docs: `docs/operations/deploy-stacks.md`); overlays remain the source of truth for cluster wiring.

## Portable purpose overlays

| Overlay | Stack CLI | Purpose |
| ------- | --------- | ------- |
| `tap-testing/` | `tap-testing` | MQTT + tap collector/stream + Redpanda + telemetry bridge (no LB/NFS) |
| `core/` | `core` | Redis + Ollama + Ollama-fast + LLM API |
| `ui/` | `ui` | Chat UI |
| `health/` | `health` | Oximeter collector |
| `dns/` | `dns` | Cluster CoreDNS |

These overlays do **not** pin lab registry hosts; `./deploy.sh` rewrites `jarvis_*` images from `REGISTRY` / `IMAGE_TAG`.

## Site / lab overlays

| Overlay | Purpose |
| ------- | ------- |
| `gpu-rig/` | PoC: namespace `jarvis-llm`, fridge NFS, registry `192.168.86.120:32000` |
| `gpu-rig-ui/` | Chat UI LoadBalancer |
| `gpu-rig-llm/` | llm-api LAN LB + feedback/adapters NFS |
| `gpu-rig-telemetry/` | Tap stack with MetalLB + NFS (sessions, CAM, recordings) |
| `gpu-rig-health/` | Oximeter in namespace `health` |
| `gpu-rig-dns/` | LAN CoreDNS for `jarvis.lan` |
| `microk8s/` | Image rewrite to `localhost:32000` for the MicroK8s registry addon |

Load site overlays via profile:

```bash
./deploy.sh --profile gpu-rig tap-testing
./deploy.sh --profile microk8s core
```

## Apply manually

```bash
kubectl apply -k k8s/overlays/tap-testing/
kubectl apply -k k8s/overlays/gpu-rig/
kubectl apply -k k8s/overlays/microk8s/
```

## Creating a new overlay for your registry

1. Copy a portable purpose overlay (e.g. `tap-testing/`) or `microk8s/` to `<your-name>/`.
2. Add site patches (LoadBalancer IPs, PVCs, NFS) as needed.
3. Either set `images:` in the overlay **or** rely on `./deploy.sh` + `REGISTRY`.
4. Point a profile or `STACK_OVERLAY_<stack>` at `k8s/overlays/<your-name>/`.
