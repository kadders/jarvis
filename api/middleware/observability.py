from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from utils.metrics import observe_http_request
from utils.request_context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    reset_request_id,
    set_request_id,
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind X-Request-ID (or X-Correlation-ID) for logs and outbound propagation."""

    async def dispatch(self, request: Request, call_next) -> Response:
        raw = request.headers.get(REQUEST_ID_HEADER) or request.headers.get(
            CORRELATION_ID_HEADER
        )
        rid = (raw or "").strip() or str(uuid.uuid4())
        token = set_request_id(rid)
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = rid
            return response
        finally:
            reset_request_id(token)


class PrometheusHTTPMiddleware(BaseHTTPMiddleware):
    """Record request counts and latency."""

    async def dispatch(self, request: Request, call_next) -> Response:
        t0 = time.perf_counter()
        response = await call_next(request)
        dt = time.perf_counter() - t0
        observe_http_request(
            request.method,
            request.url.path,
            response.status_code,
            dt,
        )
        return response
