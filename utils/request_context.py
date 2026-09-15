"""Per-request context for tracing (X-Request-ID / X-Correlation-ID)."""
from __future__ import annotations

import contextvars
from typing import Dict, Optional

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"

_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)


def get_request_id() -> Optional[str]:
    return _request_id.get()


def set_request_id(value: str) -> contextvars.Token:
    return _request_id.set(value)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id.reset(token)


def outbound_trace_headers() -> Dict[str, str]:
    """Headers to forward on outbound HTTP calls (Ollama, webhooks, etc.)."""
    rid = _request_id.get()
    if rid:
        return {REQUEST_ID_HEADER: rid}
    return {}
