# ASR API

The ASR service is a separate FastAPI app under `asr/` (default port **8001** in development).

## Features

- File-based transcription
- Directory monitor for `shared/audio_input`
- WebSocket **`/api/v1/stream`** for chunked audio (see [ASR streaming](../asr-streaming.md))

## Configuration

Uses **`ASR_MODEL_PATH`**, **`AUDIO_*`**, and **`TRANSCRIPTIONS_DIR`** — see [Environment variables](../reference/environment-variables.md).
