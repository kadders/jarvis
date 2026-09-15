# TTS API (optional)

A minimal text-to-speech HTTP API can run as a separate service (`tts/`) for future **`result.audio`** fields from the LLM pipeline.

**Status:** optional component; wire-up may be partial in this repository revision. Deploy with Compose or `k8s/tts/` when enabled.

## Planned surface

- `POST /api/v1/synthesize` — JSON body with `text`, optional `voice` / `language`
- Returns audio bytes or URL per `Content-Type` negotiation

See [Environment variables](../reference/environment-variables.md) when TTS env vars are added.
