# Feedback and fine-tuning

Jarvis learns from incorrect answers in two layers:

1. **Runtime correction memory** — negative ratings with a **correction** are stored and injected as few-shot system notes on later similar prompts.
2. **Weight updates** — export preference pairs and run **DPO + QLoRA**, then serve a new Ollama tag (e.g. **`jarvis-tuned:latest`**).

Classic PPO RLHF is out of scope on the 5× GTX 1070 PoC.

## Capture API

**`POST /api/v1/feedback`**

| Field | Notes |
| ----- | ----- |
| **`completion_id`** | From **`/v1/chat/completions`** (`chatcmpl-…`) |
| **`event_id`** | From **`/api/v1/events`** / chat UI |
| **`rating`** | `-1` / `0` / `1` |
| **`correction`** | Preferred assistant text (strongly recommended when rating is `-1`) |
| **`preferred`** / **`rejected`** | Explicit DPO pair override |
| **`source`** | `ui` \| `cursor` \| `mcp` \| `api` \| `other` |

Store path: **`JARVIS_FEEDBACK_DIR`**/`feedback.jsonl` (gpu-rig-llm: fridge NFS **`…/jarvis-llm/llm-feedback`**).

**`GET /api/v1/feedback/stats`** returns `{ count, path }`.

### Read APIs (Learning UI)

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/api/v1/feedback/summary` | Aggregates: `by_rating`, `by_source`, `by_kind`, **slices** (`all` / `ask_local` / `agent_outcome` / `mcp` / `ui` / `python_jarvis` / `arduino_wand` / `rrf_nxt` / `cad` / `mentra`) with `{ n, pos, neg, zero, judged, accuracy }`, `by_day`, `exportable_pairs`, `with_correction`. Query: **`day_limit`** (default 30). |
| `GET` | `/api/v1/feedback` | Filtered list. Query: `rating`, `source`, `kind` (`outcome`\|`other`), `slice`, `domain`, `q`, `limit` (≤200), `offset`, `order`. Items include previews + `slice_tags`. |
| `GET` | `/api/v1/feedback/{feedback_id}` | Full record + `slice_tags` (404 if missing). |

### Learning UI {#learning-ui}

Open the Chat UI (**`:8003`**) and switch to **Learning** (or `#learning`). The BFF proxies the read APIs above so the browser never hits the LLM API directly. Use it to inspect incorrect ask-local answers vs agent outcomes before exporting DPO pairs. Details: [Chat UI](../api/ui.md#learning-view).

Chat UI exposes **Helpful** / **Incorrect** on assistant turns (Incorrect prompts for a correction). MCP: **`jarvis_ask_local`** → **`jarvis_log_outcome`** (call + end result + logic) and/or **`jarvis_submit_feedback`** (always **`prefer_local`** so learning applies to the local Ollama model, not cloud fallback).

### Cursor agent trajectory capture

| Path | What gets stored |
| ---- | ---------------- |
| **`jarvis_log_outcome`** | Explicit call / result / logic from the agent |
| **`.cursor/hooks.json` → `afterMCPExecution`** | Auto-posts each `jarvis_*` MCP tool input + JSON result (`rating: 0`, `metadata.kind: outcome`) |
| **`.cursor/hooks.json` → `stop`** | On agent turn end, posts last user query + assistant text from the transcript (`rating: 1` on success, `-1` on error) |

Hook scripts live under **`.cursor/hooks/`** and POST to **`JARVIS_LLM_BASE_URL`**/`feedback` (default **`http://llm.jarvis.lan/api/v1`**). They fail open (agent continues if Jarvis is unreachable). Set **`JARVIS_LLM_BASE_URL`** in the environment if the LAN name differs.

## Export preferences

```bash
python -m training.export_preferences \
  --feedback cache/llm_feedback/feedback.jsonl \
  --out cache/llm_adapters/preferences.jsonl \
  --min-pairs 8
```

Each line: `{"prompt","chosen","rejected"}`. Export refuses to write when fewer than **`--min-pairs`** valid pairs exist.

Kubernetes (after feedback PVC exists):

```bash
kubectl -n jarvis-llm apply -f k8s/llm-train/job.yaml
```

## Golden eval

```bash
python -m training.eval_golden \
  --cases training/golden/cases.jsonl \
  --base-url http://llm.jarvis.lan/v1 \
  --model llama31-8b \
  --api-key jarvis-lan-openai
```

Domain (RRF / nxt coding) cases:

```bash
python -m training.eval_golden \
  --cases training/golden/rrf_nxt.jsonl \
  --base-url http://llm.jarvis.lan/v1 \
  --model llama31-8b \
  --api-key jarvis-lan-openai
```

## Domain coding dataset (RRF / nxt)

Curated preference pairs mined from the sibling **NeXT** (MOS-nxt) checkout — macros, `GCODE.md`, `docs/CODE.md`, RRF meta/line-length/3.7 migration docs, `OM_GLOBAL_SIZE.md`, `macros/system/nxt-vars.g`, and nxt globals manifest descriptions. Pair kinds: `code_style`, `gcode_api`, `macro`, `rrf_meta`, `rrf_syntax`, `generation_style`, `nxt_globals`, `macro_skeleton`. Output lives under **`training/datasets/rrf_nxt/`** (GPL-3.0 provenance; see that folder’s README).

```bash
# Regenerate from a local NeXT tree:
python -m training.build_rrf_nxt_dataset \
  --next-root ../NeXT \
  --out-dir training/datasets/rrf_nxt

# Validate for DPO without GPU:
python -m training.train_dpo --dry-run --min-pairs 8 \
  --preferences training/datasets/rrf_nxt/preferences.jsonl \
  --out-dir cache/llm_adapters/run_rrf_nxt_v2 \
  --ollama-base-tag llama3.2:3b \
  --ollama-tag jarvis-tuned:latest
```

Each preference line is `{"prompt","chosen","rejected"}` (same schema as exported feedback). Merge with live operator prefs by concatenating JSONL before a real train if desired.

## Python style (Black)

Jarvis Python uses **[Black](https://black.readthedocs.io/)** (`line-length = 88`, `target-version = py310` in **`pyproject.toml`**). Generated code from **`code_generation`** events and MCP corrections should be Black-formatted so few-shot injection and DPO export stay consistent.

## Domain coding dataset (Python / Jarvis)

Mined from **`training/datasets/python_jarvis/STYLE.md`**, Jarvis `api/` / `utils/` / `jarvis_mcp/` / `tap_collector/`, sibling **tap-testing** (`tap_testing/`, MQTT docs), and **tiny-ocr** client. Pair kinds include **`black_format`**, **`python_style`**, **`fastapi`**, **`mqtt_telemetry`**, **`jarvis_conventions`**.

```bash
python -m training.build_python_dataset \
  --jarvis-root . \
  --tap-testing-root ../tap-testing \
  --tiny-ocr-root ../tiny-ocr \
  --out-dir training/datasets/python_jarvis
```

Golden eval: **`training/golden/python_jarvis.jsonl`**.

## Public Python DPO corpus

Filtered Hugging Face datasets (separate dir for licensing): **`training/datasets/python_public/`** via **`training/fetch_public_python_data.py`**. Sources: **NextWealth/Python-DPO**, **quangduc1112001/python-code-DPO-fine-tune**, filtered **PotatoHD/code-edit-dpo**. CI uses **`--use-fixtures`**; operators pull live data with HF **`datasets`** (see **`training/requirements-train.txt`**).

```bash
python -m training.fetch_public_python_data --out-dir training/datasets/python_public --max-total 1500
```

## Domain coding dataset (Arduino / ESP32)

Mined from **`training/datasets/arduino_wand/STYLE.md`**, **`wand/edge/esp32/`**, and **tiny-ocr** firmware/hardware docs. Pair kinds: **`arduino_style`**, **`esp32_wifi`**, **`platformio`**, **`http_json`**, **`non_blocking`**.

```bash
python -m training.build_arduino_dataset \
  --jarvis-root . \
  --tiny-ocr-root ../tiny-ocr \
  --out-dir training/datasets/arduino_wand
```

Golden eval: **`training/golden/arduino_wand.jsonl`**.

## Domain dataset (CAD / CAM)

Curated preference pairs for Fusion Design MCP tool-use, shop math (chipload / GUID↔`post-process.number`↔RRF `Tn`), post hygiene, and Manufacture coaching boundaries. Seeded from **`training/datasets/cad/STYLE.md`**, curated Q&A, [Fusion 360 MCP](fusion360-mcp.md), [tap-collector](../services/tap-collector.md), and `tap_collector/models/cutting_force.py`. Pair kinds include **`fusion_tool_use`**, **`fusion_timeout`**, **`scene_recovery`**, **`tool_map`**, **`chipload`**, **`cutting_force`**, **`cam_gcode_resolve`**, **`post_hygiene`**, **`manufacture_setup`**, **`toolpath`**, **`cad_style`**.

```bash
python -m training.build_cad_dataset \
  --jarvis-root . \
  --out-dir training/datasets/cad
```

Golden eval: **`training/golden/cad.jsonl`**.

```bash
python -m training.eval_golden \
  --cases training/golden/cad.jsonl \
  --base-url http://llm.jarvis.lan/v1 \
  --model llama31-8b \
  --api-key jarvis-lan-openai
```

Log Fusion / CAM agent outcomes with **`metadata.domain: "cad"`** (`jarvis_log_outcome` / feedback).

## Domain dataset (MentraOS)

Curated preference pairs for Mentra Miniapp SDK (`@mentra/miniapp`) and Mentra Bluetooth SDK coaching from [docs.mentraglass.com](https://docs.mentraglass.com/) (`llms.txt` index). Seeded from **`training/datasets/mentra/STYLE.md`** and curated Q&A. Pair kinds include **`sdk_choice`**, **`legacy_cloud`**, **`two_layer`**, **`startup`**, **`session`**, **`display`**, **`scaffold`**, **`cli`**, **`distribution`**, **`bluetooth`**, **`channels`**, **`mentra_style`**. OEM engine APIs are out of scope.

```bash
python -m training.build_mentra_dataset \
  --jarvis-root . \
  --out-dir training/datasets/mentra
```

Golden eval: **`training/golden/mentra.jsonl`**.

```bash
python -m training.eval_golden \
  --cases training/golden/mentra.jsonl \
  --base-url http://llm.jarvis.lan/v1 \
  --model llama31-8b \
  --api-key jarvis-lan-openai
```

Log Mentra agent outcomes with **`metadata.domain: "mentra"`** (`jarvis_log_outcome` / feedback).

## Merge corpora before train

```bash
python -m training.merge_preferences \
  --out cache/llm_adapters/preferences_merged.jsonl \
  --jarvis-ratio-min 0.4 \
  training/datasets/rrf_nxt/preferences.jsonl \
  training/datasets/python_jarvis/preferences.jsonl \
  training/datasets/python_public/preferences.jsonl \
  training/datasets/arduino_wand/preferences.jsonl \
  training/datasets/cad/preferences.jsonl \
  training/datasets/mentra/preferences.jsonl
```

Feedback records may set **`metadata.domain`**: `python_jarvis` | `arduino_wand` | `rrf_nxt` | `cad` | `mentra` | `general`. The Learning UI and **`GET /api/v1/feedback`** support **`domain`** / slice filters. MCP: optional **`domain`** on **`jarvis_log_outcome`** / **`jarvis_submit_feedback`**.

## DPO / QLoRA train

```bash
# Validate dataset + write Ollama Modelfile stub (no GPU):
python -m training.train_dpo --dry-run --min-pairs 1 \
  --preferences cache/llm_adapters/preferences.jsonl \
  --out-dir cache/llm_adapters/run

# Real train (needs training/requirements-train.txt + CUDA):
python -m training.train_dpo --min-pairs 8 --max-steps 100
```

**Cluster (gpu-rig):** build/push the CUDA image, stage preferences onto the adapters PVC, free one GPU, then apply the Job:

```bash
docker build -t 192.168.86.120:32000/jarvis_llm_train_cuda:latest \
  -f training/Dockerfile.cuda .
docker push 192.168.86.120:32000/jarvis_llm_train_cuda:latest

cp cache/llm_adapters/preferences_merged.jsonl \
  /media/kad/data/k8s/jarvis-llm/llm-adapters/preferences.jsonl

kubectl -n jarvis-llm scale sts/ollama-fast --replicas=1
kubectl -n jarvis-llm delete job jarvis-llm-dpo-gpu --ignore-not-found
kubectl -n jarvis-llm apply -f k8s/llm-train/job-gpu.yaml
```

The Job uses ungated **`unsloth/Llama-3.2-3B-Instruct`** (no HF token; 8B OOMs on a single 1070). Current template writes under **`/data/adapters/run_jarvis_v5`** (merged RRF + Python + Arduino + CAD + Mentra corpus). **`--max-steps`** is **280** for the expanded corpus.

GPU Job template: **`k8s/llm-train/job-gpu.yaml`**. Free one 1070 first (scale **`ollama-fast`** down or reduce big-tier GPUs) — see [LLM tiers and hardware](llm-tiers-and-hardware.md).

### Serve the tuned model

1. Merge the LoRA adapter and convert to GGUF (cluster Job):

```bash
kubectl -n jarvis-llm delete job jarvis-llm-dpo-gpu jarvis-llm-export-ollama --ignore-not-found
kubectl -n jarvis-llm apply -f k8s/llm-train/job-export-ollama.yaml
# Artifacts: …/llm-adapters/run_jarvis_v5/{merged/,jarvis-tuned-q8_0.gguf,Modelfile,export_meta.json}
```

   Locally (same CUDA image / train deps): `python -m training.export_ollama_tuned --run-dir …/run_jarvis_v5`.

2. Publish on the **big** Ollama pod (tuned tier routes there; **overwrite** the same tag):

```bash
RUN=/media/kad/data/k8s/jarvis-llm/llm-adapters/run_jarvis_v5
POD=$(kubectl -n jarvis-llm get pod -l app.kubernetes.io/name=ollama -o jsonpath='{.items[0].metadata.name}')
kubectl -n jarvis-llm exec "$POD" -- mkdir -p /tmp/jarvis-tuned
kubectl -n jarvis-llm cp "$RUN/jarvis-tuned-q8_0.gguf" "$POD:/tmp/jarvis-tuned/jarvis-tuned-q8_0.gguf"
kubectl -n jarvis-llm cp "$RUN/Modelfile" "$POD:/tmp/jarvis-tuned/Modelfile"
kubectl -n jarvis-llm exec "$POD" -- \
  sh -c 'cd /tmp/jarvis-tuned && ollama create jarvis-tuned:latest -f Modelfile'
```

3. Keep **`MODEL_NAME_TUNED=jarvis-tuned:latest`** in **`k8s/overlays/gpu-rig-llm/patch-configmap-openai.yaml`** (already set after the first promote). Re-apply only if unset.

4. In Cursor, select **`jarvis-tuned:latest`** from **`GET /v1/models`**.

Adapters PVC: fridge **`…/jarvis-llm/llm-adapters`** (`llm-adapters` claim in gpu-rig-llm overlay).

**NFS note:** fridge Ollama model dirs may be root-owned with NFS `root_squash`, so in-pod `ollama create` can hit `permission denied` on `…/models/blobs`. From the NFS host: `sudo chmod -R a+rwX /media/kad/data/k8s/jarvis-llm/ollama-big/models` (and the matching `ollama-fast-*` trees if publishing there).

## Revision note

**2026-07-28** — Cursor trajectory capture: MCP **`jarvis_log_outcome`**, project hooks under **`.cursor/hooks/`**, and feedback `metadata.kind: outcome`.

**2026-07-28** — Domain RRF/nxt coding dataset: **`training/build_rrf_nxt_dataset.py`**, **`training/datasets/rrf_nxt/`**, golden cases **`training/golden/rrf_nxt.jsonl`**.

**2026-07-29** — Serve path: **`training/export_ollama_tuned.py`** + **`k8s/llm-train/job-export-ollama.yaml`** (merge LoRA → GGUF → `ollama create jarvis-tuned:latest`).
**2026-07-29** — Second tune: formatting/generation syntax + nxt globals (`rrf_syntax`, `generation_style`, `nxt_globals`, `macro_skeleton`); train/export under **`run_rrf_nxt_v2`**; overwrite **`jarvis-tuned:latest`**.

**2026-08-31** — Python (Black) + Arduino datasets: **`build_python_dataset`**, **`build_arduino_dataset`**, **`fetch_public_python_data`**, **`merge_preferences`**; golden **`python_jarvis.jsonl`** / **`arduino_wand.jsonl`**; feedback **`metadata.domain`**; merged train **`run_jarvis_v3`**.

**2026-10-01** — CAD/CAM domain: **`build_cad_dataset`**, **`training/datasets/cad/`** (88 pairs), golden **`cad.jsonl`**; feedback domain **`cad`**; merged train **`run_jarvis_v4`** (565 pairs after jarvis-ratio cap) → **`jarvis-tuned:latest`**. Baseline golden on tuned 3B: cad **8/30**, rrf sample **5/15** — weight transfer on the 3B tag is weak for terse quizzes; rely on **`metadata.domain: cad`** few-shot on **`llama3.1:8b`** for Fusion Design agents, and grow live CAD corrections before the next CAD-heavier merge.

**2026-11-04** — MentraOS domain: **`build_mentra_dataset`**, **`training/datasets/mentra/`**, golden **`mentra.jsonl`**; feedback domain **`mentra`** (Miniapp + Bluetooth SDK; OEM deferred); merged train **`run_jarvis_v5`** → **`jarvis-tuned:latest`**. Prefer **`metadata.domain: mentra`** few-shot on **`llama3.1:8b`** for Mentra coaching; grow live Mentra corrections before the next Mentra-heavier merge.
