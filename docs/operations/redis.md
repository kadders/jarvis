# Redis for idempotency

The LLM API can store **`Idempotency-Key`** replay state in **Redis** so duplicate submissions (retries, multiple replicas) do not re-run the model. Use **`IDEMPOTENCY_BACKEND=redis`** and a valid **`REDIS_URL`**.

For variable definitions, see [Environment variables](../reference/environment-variables.md).

## Configuration by environment

| Situation | `IDEMPOTENCY_BACKEND` | Typical `REDIS_URL` | Notes |
| --------- | --------------------- | ------------------- | ----- |
| **Kubernetes** (full stack) | `redis` | `redis://redis:6379/0` | ClusterIP Service named **`redis`** in the same namespace as **`llm-api`**. Uses in-cluster DNS. |
| **Docker Compose** (full stack) | `redis` | `redis://redis:6379/0` | Compose service name **`redis`**; AOF data under **`cache/redis/data`**. |
| **LLM on the host, Redis in a container** | `redis` | `redis://127.0.0.1:6379/0` | Publish port **6379** from the container; ensure the host firewall allows local connections. |
| **Single process / CI / quick tests** | `memory` | *(omit)* | No shared store; not safe with multiple workers or replicas. |

### Authentication (production)

For clusters that enforce a password, use a Redis URL with credentials and store the secret in Kubernetes **`Secret`** (or your secret manager), not in **`ConfigMap`**:

`redis://:YOUR_PASSWORD@redis:6379/0`

TLS URLs follow the `rediss://` scheme when your Redis proxy or cloud offering requires it.

## Kubernetes

Redis is part of the default manifests under **`k8s/redis/`** and is included from **`k8s/kustomization.yaml`** at the repository root.

Apply the stack:

```bash
kubectl apply -k k8s/
```

The **`llm-api`** **`ConfigMap`** sets **`IDEMPOTENCY_BACKEND=redis`** and **`REDIS_URL=redis://redis:6379/0`**.

The Redis pod mounts **`PersistentVolumeClaim` `redis-data`** at **`/data`** (AOF enabled). If provisioning fails, ensure a **StorageClass** exists or edit **`k8s/redis/pvc.yaml`** at the repository root to set **`spec.storageClassName`**.

More context: [Kubernetes operations](kubernetes.md).

## Docker Compose (recommended local stack)

The Compose file defines a **`redis`** service and wires **`llm_api`** with **`depends_on`** (healthcheck) and env vars.

```bash
docker compose up -d redis
docker compose up -d llm_api
```

Or bring up the full stack; **`llm_api`** waits until Redis is healthy.

Details: [Docker and Compose](docker.md).

## Standalone container (Docker or Podman)

Use this when you run **uvicorn on the host** (or another container) but want Redis in its own container.

```bash
mkdir -p cache/redis/data

docker run -d \
  --name jarvis-redis \
  -p 6379:6379 \
  -v "$(pwd)/cache/redis/data:/data" \
  redis:7-alpine \
  redis-server --appendonly yes
```

Podman equivalent:

```bash
podman run -d \
  --name jarvis-redis \
  -p 6379:6379 \
  -v "$(pwd)/cache/redis/data:/data" \
  docker.io/library/redis:7-alpine \
  redis-server --appendonly yes
```

Then point the LLM API at **`REDIS_URL=redis://127.0.0.1:6379/0`**.

To require a password locally:

```bash
docker run -d --name jarvis-redis -p 6379:6379 \
  redis:7-alpine \
  redis-server --appendonly yes --requirepass mydevsecret
```

Use **`REDIS_URL=redis://:mydevsecret@127.0.0.1:6379/0`**.

## Verifying connectivity

From a shell with **`redis-cli`**:

```bash
redis-cli -u redis://127.0.0.1:6379/0 ping
```

Expect **`PONG`**.
