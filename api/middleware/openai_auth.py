"""Optional Bearer auth for OpenAI-compat (/v1) routes."""
from __future__ import annotations

from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from utils.config import get_settings


class OpenAICompatAuthMiddleware(BaseHTTPMiddleware):
    """
    When JARVIS_OPENAI_API_KEY is set, require Authorization: Bearer <key>
    (or x-api-key) for paths under /v1/.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if not path.startswith("/v1"):
            return await call_next(request)
        settings = get_settings()
        expected: Optional[str] = (settings.jarvis_openai_api_key or "").strip() or None
        if not expected:
            return await call_next(request)

        auth = request.headers.get("authorization") or ""
        token = None
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if not token:
            token = (request.headers.get("x-api-key") or "").strip() or None

        if token != expected:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "message": "Invalid API key",
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    }
                },
            )
        return await call_next(request)
