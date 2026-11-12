"""Tests for OpenAI-compatible /v1 endpoints and feedback API."""
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from api.main import app
from utils.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_FEEDBACK_DIR", str(tmp_path / "feedback"))
    monkeypatch.delenv("JARVIS_OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_list_models(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    ids = {m["id"] for m in data["data"]}
    assert "mock_model" in ids or len(ids) >= 1


def test_chat_completions_sync(client):
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock_model",
            "messages": [{"role": "user", "content": "Hello Jarvis"}],
            "stream": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"]
    assert body["id"].startswith("chatcmpl-")
    assert "jarvis" in body


def test_chat_completions_stream(client):
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "mock_model",
            "messages": [{"role": "user", "content": "stream please"}],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "data: " in text
    assert "[DONE]" in text
    # At least one JSON chunk
    lines = [ln for ln in text.splitlines() if ln.startswith("data: ") and "[DONE]" not in ln]
    assert lines
    chunk = json.loads(lines[0][len("data: ") :])
    assert chunk["object"] == "chat.completion.chunk"


def test_openai_auth_required_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_FEEDBACK_DIR", str(tmp_path / "feedback"))
    monkeypatch.setenv("JARVIS_OPENAI_API_KEY", "secret-key")
    get_settings.cache_clear()
    with TestClient(app) as c:
        denied = c.get("/v1/models")
        assert denied.status_code == 401
        ok = c.get("/v1/models", headers={"Authorization": "Bearer secret-key"})
        assert ok.status_code == 200
        # Events API remains open
        ev = c.post(
            "/api/v1/events",
            json={"event_type": "text_query", "content": {"text": "x"}},
        )
        assert ev.status_code == 200
    monkeypatch.delenv("JARVIS_OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()


def test_feedback_from_completion(client):
    chat = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock_model",
            "messages": [{"role": "user", "content": "capital of france"}],
        },
    )
    assert chat.status_code == 200
    cid = chat.json()["id"]
    fb = client.post(
        "/api/v1/feedback",
        json={
            "completion_id": cid,
            "rating": -1,
            "correction": "Paris",
            "source": "ui",
        },
    )
    assert fb.status_code == 200
    assert fb.json()["feedback_id"]
    stats = client.get("/api/v1/feedback/stats")
    assert stats.status_code == 200
    assert stats.json()["count"] >= 1


def test_feedback_from_event(client):
    ev = client.post(
        "/api/v1/events",
        json={"event_type": "text_query", "content": {"text": "2+2"}},
    )
    eid = ev.json()["event_id"]
    fb = client.post(
        "/api/v1/feedback",
        json={"event_id": eid, "rating": 1, "source": "mcp"},
    )
    assert fb.status_code == 200


_PING_TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Health check",
        "parameters": {"type": "object", "properties": {}},
    },
}


def test_chat_completions_tools_mock(client):
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock_model",
            "messages": [{"role": "user", "content": "ping Fusion"}],
            "tools": [_PING_TOOL],
            "stream": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    msg = choice["message"]
    assert msg.get("content") in (None, "")
    assert msg["tool_calls"]
    assert msg["tool_calls"][0]["function"]["name"] == "ping"
    assert msg["tool_calls"][0]["id"]


def test_chat_completions_tool_followup(client):
    """Assistant tool_calls + role=tool messages are accepted (no 422)."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock_model",
            "messages": [
                {"role": "user", "content": "ping Fusion"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "ping", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_abc",
                    "content": '{"ok": true, "status": "pong"}',
                },
            ],
            "tools": [_PING_TOOL],
            "stream": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    # Mock still emits a tool call when tools are present.
    assert body["choices"][0]["finish_reason"] == "tool_calls"


def test_chat_completions_tools_stream(client):
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "mock_model",
            "messages": [{"role": "user", "content": "call ping"}],
            "tools": [_PING_TOOL],
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "[DONE]" in text
    lines = [ln for ln in text.splitlines() if ln.startswith("data: ") and "[DONE]" not in ln]
    payloads = [json.loads(ln[len("data: ") :]) for ln in lines]
    saw_tool_delta = False
    saw_tool_finish = False
    for chunk in payloads:
        choice = chunk["choices"][0]
        delta = choice.get("delta") or {}
        if delta.get("tool_calls"):
            saw_tool_delta = True
            assert delta["tool_calls"][0]["function"]["name"] == "ping"
        if choice.get("finish_reason") == "tool_calls":
            saw_tool_finish = True
    assert saw_tool_delta
    assert saw_tool_finish
