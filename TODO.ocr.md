# TODO: Jarvis OCR service

Tracked implementation lives in **`docs/services/ocr.md`** (checklist + revision history).

**Summary**

- **REST:** `POST /api/v1/images`, `DELETE /api/v1/images/{image_id}`, `GET /api/v1/ocr/health`, `GET /api/v1/ocr/models/{name}`, `POST /api/v1/ocr/recognize`.
- **MCP tools:** `jarvis_ocr_recognize`, `jarvis_ocr_health`; transports **`JARVIS_MCP_TRANSPORT`** (`stdio` \| `streamable-http` \| `both`).
- **Docs:** [`docs/mcp-payloads.md`](docs/mcp-payloads.md), [`docs/mcp.md`](docs/mcp.md), [`docs/reference/environment-variables.md`](docs/reference/environment-variables.md).

**Consumers**

- [`../tiny-ocr`](../tiny-ocr) firmware (REST + HTTP MCP).
- Cursor / MCP hosts on stdio.
- Ad-hoc scripts.
