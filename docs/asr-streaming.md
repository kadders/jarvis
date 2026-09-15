# ASR WebSocket streaming

**Scope:** This page documents **speech / audio** streaming on the **ASR** service only. **OCR and object detection** live on the **LLM API** (`POST /api/v1/ocr/recognize`, etc.) and **do not** use this WebSocket—there is no shared “ASR socket for vision.”

The ASR service exposes **`/api/v1/stream`** (WebSocket) for live audio chunks.

## Session protocol

1. Connect to the WebSocket.
2. Send a **JSON text** frame to open a session:

   ```json
   {"type": "stream_open", "session_id": "optional-client-id"}
   ```

3. Server responds with:

   ```json
   {"type": "stream_ack", "session_id": "<uuid>"}
   ```

4. Send **binary** frames containing audio bytes (e.g. WAV). Each chunk is transcribed; results are JSON messages shaped like `{ "status": "success", "text": "...", ... }`.

5. Close the logical session with a **JSON text** frame:

   ```json
   {"type": "stream_close"}
   ```

6. Server responds with `{"type": "stream_closed", "session_id": "..."}`.

## Legacy mode

If **`ASR_STREAM_LEGACY_BYTES`** is set to **`true`** (default), clients may send binary chunks **before** `stream_open`; behavior matches the older firehose style. When **`false`**, the server returns an error JSON if binary arrives without an active session.

## See also

- [ASR API](api/asr.md)

## Not for vision / OCR

Jarvis does **not** route camera frames or OCR through this socket. For text + object detection on images, use the **LLM API** [OCR service](services/ocr.md) (REST and/or MCP tools)—a separate **vision** WebSocket, if added later, would be documented on its own and exposed from the LLM API (or a dedicated vision gateway), not from `asr/`.
