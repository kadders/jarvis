"""Prometheus metrics (optional /metrics endpoint)."""
from __future__ import annotations

import re

from prometheus_client import Counter, Histogram

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


def metrics_path_group(path: str) -> str:
    """Reduce cardinality: collapse UUID path segments."""
    if path in ("/", "/health", "/metrics"):
        return path
    parts = path.strip("/").split("/")
    out: list[str] = []
    for p in parts:
        if _UUID_RE.match(p):
            out.append("{id}")
        else:
            out.append(p)
    return "/" + "/".join(out) if out else "/"


HTTP_REQUESTS = Counter(
    "jarvis_http_requests_total",
    "HTTP requests",
    ["method", "path_group", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "jarvis_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path_group"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

EVENTS_POST_TOTAL = Counter(
    "jarvis_events_post_total",
    "POST /api/v1/events outcomes",
    ["outcome"],
)

IDEMPOTENCY_REPLAY_TOTAL = Counter(
    "jarvis_idempotency_replay_total",
    "Responses served from idempotency store (replay)",
)

OCR_RECOGNIZE_SECONDS = Histogram(
    "jarvis_ocr_recognize_duration_seconds",
    "Handler time for POST /api/v1/ocr/recognize",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 60.0, 120.0),
)

OCR_RECOGNIZE_TOTAL = Counter(
    "jarvis_ocr_recognize_total",
    "OCR recognize completed responses by outcome class",
    ["outcome"],
)

OPENAI_CHAT_TOTAL = Counter(
    "jarvis_openai_chat_total",
    "POST /v1/chat/completions outcomes",
    ["outcome"],
)

FALLBACK_TOTAL = Counter(
    "jarvis_llm_fallback_total",
    "Completions served via cloud fallback after primary failure",
    ["outcome"],
)

FEEDBACK_POST_TOTAL = Counter(
    "jarvis_feedback_post_total",
    "POST /api/v1/feedback outcomes",
    ["outcome"],
)


def record_ocr_recognize(status_code: int, duration_s: float) -> None:
    if status_code == 200:
        outcome = "success"
    elif 400 <= status_code < 500:
        outcome = "client_error"
    else:
        outcome = "server_error"
    OCR_RECOGNIZE_TOTAL.labels(outcome).inc()
    OCR_RECOGNIZE_SECONDS.observe(duration_s)


def observe_http_request(method: str, path: str, status_code: int, duration_s: float) -> None:
    group = metrics_path_group(path)
    HTTP_REQUESTS.labels(method, group, str(status_code)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method, group).observe(duration_s)
