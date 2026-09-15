from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from api.main import app
from jarvis_mcp.server import _http_error_json


def test_health_echoes_request_id() -> None:
    with TestClient(app) as client:
        r = client.get("/health", headers={"X-Request-ID": "client-rid-1"})
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID") == "client-rid-1"


def test_metrics_exposes_jarvis_series() -> None:
    with TestClient(app) as client:
        r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "jarvis_http_requests_total" in body


def test_http_error_json_shape() -> None:
    req = httpx.Request("POST", "http://127.0.0.1:8000/api/v1/events")
    resp = httpx.Response(
        502,
        request=req,
        headers={"X-Request-ID": "abc"},
        content=b"nope",
    )
    d = json.loads(_http_error_json(resp))
    assert d["error"] == "http_error"
    assert d["status_code"] == 502
    assert d["request_id"] == "abc"
    assert "body_preview" in d
