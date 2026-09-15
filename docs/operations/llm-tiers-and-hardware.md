# LLM tiers and hardware

Jarvis supports a **default (“big”)** Ollama tier and an optional **fast** tier selected per request with **`metadata.model_tier": "fast"`** (see [LLM events API](../api/llm-events.md)).

| Tier | Default tag | Role |
| ---- | ----------- | ---- |
| **Big** | **`llama3.1:8b`** | Default completions on the multi-GPU Ollama Deployment (Pascal-safe with pinned **`ollama/ollama:0.4.7`**). |
| **Fast** | **`llama3.2:3b`** | Cheap / MCP-style traffic; StatefulSet replicas, one GPU each when scheduled. |

**`gpt-oss:20b`** remains the intended future big-tier model once Ollama is bumped past **0.4.7** (pull currently returns HTTP **412**). See [Kubernetes — model pins](kubernetes.md#model-pins-and-gpt-oss).

---

## Fast tier: **`llama3.2:3b`**

| Aspect | Practical guidance |
| ------ | ------------------ |
| **Role** | MCP-style prompts, shallow reasoning, cheap traffic. |
| **VRAM** | Comfortable on **GTX 1070 8 GB**; also runs on CPU (much slower). |
| **Disk** | **~2–4 GB** per fast PVC (one pull per StatefulSet ordinal). |
| **Latency** | Sub-second class on GPU; multiple seconds on CPU. |

Pull (Compose profile or cluster):

```bash
docker compose --profile multi-llm exec ollama_fast ollama pull llama3.2:3b
# Kubernetes (gpu-rig):
# kubectl -n jarvis-llm exec ollama-fast-0 -- ollama pull llama3.2:3b
```

---

## Big tier: **`llama3.1:8b`** (current) / **`gpt-oss:20b`** (roadmap)

On the **5× GTX 1070** PoC, the big Deployment requests **3 GPUs** and serves **`llama3.1:8b`**. That matches the live **`jarvis-llm`** namespace.

When moving to **`gpt-oss:20b`**:

1. Bump **`ollama/ollama`** image tags (Compose + both K8s packages) and re-test FlashAttention-off on Pascal.
2. Set **`MODEL_NAME=gpt-oss:20b`** in the ConfigMap / Compose env.
3. Pull once onto PVC **`ollama-data`**.

---

## CPU-only vs GPU

- **CPU-only:** Prefer **`llama3.2:3b`** (or mock). Large defaults are poor interactive latency on CPU.
- **GPU (PoC):** Attach NVIDIA devices; keep **`OLLAMA_FLASH_ATTENTION=0`** on **Pascal** (GTX 10xx).

---

## Five GTX 1070 (8 GB) layout

| Pod | GPUs | Model |
| --- | ---- | ----- |
| **`ollama`** (Deployment) | 3 | **`llama3.1:8b`** |
| **`ollama-fast`** (StatefulSet ×2) | 1 each | **`llama3.2:3b`** |

Models persist on PVCs (fridge NFS via **`k8s/overlays/gpu-rig/`**). Operational details: [Kubernetes](kubernetes.md).

### Freeing a GPU for DPO/QLoRA

All five GPUs are reserved for inference by default. Before applying **`k8s/llm-train/job-gpu.yaml`**, free one device — for example scale **`ollama-fast`** to **1** replica, or temporarily reduce the big Deployment’s GPU request from **3** to **2**. See [Feedback and fine-tuning](feedback-and-finetune.md).

---

## Configuration reference

| Variable | Typical value |
| -------- | ------------- |
| **`MODEL_NAME`** | **`llama3.1:8b`** |
| **`MODEL_NAME_FAST`** | **`llama3.2:3b`** |
| **`OLLAMA_FAST_BASE_URL`** | e.g. `http://ollama-fast:11434/v1` |

See [Environment variables](../reference/environment-variables.md).
