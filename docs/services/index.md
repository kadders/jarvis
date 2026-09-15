# Services

| Service | Source | Typical port |
| ------- | ------ | ------------ |
| LLM API | Repo root `api/` | 8000 |
| ASR | `asr/` | 8001 |
| Wand | `wand/` | 8002 |
| Chat UI | `ui/` | 8003 |
| Tap collector | `tap_collector/` | 8004 (Compose profile **`telemetry`**) |
| Ollama | `ollama/ollama` image | 11434 |
| Redis | `redis:7-alpine` | 6379 |
| Mosquitto | `eclipse-mosquitto` (profile **`telemetry`**) | 1883 |
| Docs | MkDocs / `Dockerfile.docs` | 8080 (example) |
| TTS | `tts/` (optional) | 8002 (stub; conflicts with Wand if both exposed) |
| OCR | `api/` + `jarvis_mcp/` | REST **8000** (`/api/v1/images`, `/api/v1/ocr/*`), MCP HTTP **8765** (optional `jarvis_mcp` compose service) |

Kubernetes layouts live under `k8s/<service>/`.

- **[OCR service specification](ocr.md)** — REST image UUID + MCP `jarvis_ocr_recognize` (dual MCP transport).
- **[Tap collector](tap-collector.md)** — MQTT ingest from sibling **tap-testing** (accel batches, motion, analysis, optional Modbus); CSV/ZIP import; Fusion tool segments, replay sim, and resonance / RPM analytics SPA.
- **[Tap stream](tap-stream.md)** — Cluster ffmpeg/RTMP from Pi MJPEG + MQTT `video_overlay`.
- **[Klipper telemetry (future)](klipper-telemetry.md)** — Moonraker `print/…/motion` adapter plan.
- **[Wand](../wand/index.md)** — Interactive wand gesture + incantation fusion.
- **[Chat UI](../api/ui.md)** — Browser chat BFF for LLM tiers.
