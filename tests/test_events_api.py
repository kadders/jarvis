import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from api.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_process_event_text_query_envelope(client):
    event_data = {
        "event_type": "text_query",
        "content": {"text": "What is the capital of France?"},
        "source": "test_client",
    }
    response = client.post("/api/v1/events", json=event_data)
    assert response.status_code == 200
    data = response.json()
    assert "event_id" in data
    assert data["status"] == "success"
    envelope = data["response"]
    assert "request" in envelope and "result" in envelope and "meta" in envelope
    assert envelope["request"]["event_type"] == "text_query"
    assert envelope["result"]["text"]
    assert envelope["meta"]["backend"] == "mock"


def test_idempotency_replay(client):
    event_data = {
        "event_type": "text_query",
        "content": {"text": "idem test"},
    }
    headers = {"Idempotency-Key": "k-test-1"}
    r1 = client.post("/api/v1/events", json=event_data, headers=headers)
    r2 = client.post("/api/v1/events", json=event_data, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["event_id"] == r2.json()["event_id"]
    assert r1.json()["response"] == r2.json()["response"]


def test_idempotency_body_mismatch_409(client):
    headers = {"Idempotency-Key": "k-test-2"}
    r1 = client.post(
        "/api/v1/events",
        json={"event_type": "text_query", "content": {"text": "a"}},
        headers=headers,
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/api/v1/events",
        json={"event_type": "text_query", "content": {"text": "b"}},
        headers=headers,
    )
    assert r2.status_code == 409


def test_get_event_status(client):
    event_data = {
        "event_type": "text_query",
        "content": {"text": "poll me"},
    }
    create = client.post("/api/v1/events", json=event_data)
    assert create.status_code == 200
    event_id = create.json()["event_id"]
    status_response = client.get(f"/api/v1/events/{event_id}")
    assert status_response.status_code == 200
    status_data = status_response.json()
    assert status_data["event_id"] == event_id
    assert status_data["status"] == "completed"
    assert status_data["response"]["meta"]["backend"] == "mock"


def test_get_nonexistent_event(client):
    response = client.get("/api/v1/events/nonexistent-id")
    assert response.status_code == 404


def test_mock_routes_to_default_tier(client):
    """No metadata.model_tier -> mock envelope reports tier=default."""
    event = {"event_type": "text_query", "content": {"text": "hello"}}
    r = client.post("/api/v1/events", json=event)
    assert r.status_code == 200
    assert r.json()["response"]["meta"]["tier"] == "default"


def test_mock_routes_to_fast_tier_when_requested(client):
    event = {
        "event_type": "text_query",
        "content": {"text": "hello"},
        "metadata": {"model_tier": "fast"},
    }
    r = client.post("/api/v1/events", json=event)
    assert r.status_code == 200
    assert r.json()["response"]["meta"]["tier"] == "fast"


def _fake_completion(model: str, text: str):
    fake = MagicMock()
    fake.model = model
    fake.usage = None
    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = "stop"
    fake.choices = [choice]
    return fake


def test_ollama_path_mocked():
    """Ollama branch returns structured envelope when client is mocked."""
    from models.llm_handler import LLMHandler

    with patch("openai.OpenAI") as mock_openai:
        instance = MagicMock()
        instance.chat.completions.create.return_value = _fake_completion(
            "gpt-oss:20b", "Paris is the capital."
        )
        mock_openai.return_value = instance

        h = LLMHandler(
            model_path="/tmp",
            model_type="ollama",
            model_name="gpt-oss:20b",
            ollama_base_url="http://localhost:11434/v1",
        )
        eid, env = h.process_event(
            {"event_type": "text_query", "content": {"text": "capital of France?"}}
        )
        assert len(eid) > 0
        assert env["meta"]["backend"] == "ollama"
        assert env["meta"]["tier"] == "default"
        assert "Paris" in env["result"]["text"]


def test_ollama_hybrid_routes_to_fast_tier():
    """metadata.model_tier=fast -> fast Ollama client is used, big is not."""
    from models.llm_handler import LLMHandler

    big = MagicMock()
    big.chat.completions.create.return_value = _fake_completion(
        "gpt-oss:20b", "big tier"
    )
    fast = MagicMock()
    fast.chat.completions.create.return_value = _fake_completion(
        "llama3.1:8b", "fast tier"
    )

    with patch("openai.OpenAI", side_effect=[big, fast]):
        h = LLMHandler(
            model_path="/tmp",
            model_type="ollama",
            model_name="gpt-oss:20b",
            ollama_base_url="http://big:11434/v1",
            model_name_fast="llama3.1:8b",
            ollama_fast_base_url="http://fast:11434/v1",
        )

        # Default tier hits big.
        _, env_default = h.process_event(
            {"event_type": "text_query", "content": {"text": "hi"}}
        )
        assert env_default["meta"]["tier"] == "default"
        assert env_default["result"]["text"] == "big tier"
        big.chat.completions.create.assert_called_once()
        fast.chat.completions.create.assert_not_called()

        # Explicit fast tier hits fast.
        _, env_fast = h.process_event(
            {
                "event_type": "text_query",
                "content": {"text": "quick"},
                "metadata": {"model_tier": "fast"},
            }
        )
        assert env_fast["meta"]["tier"] == "fast"
        assert env_fast["result"]["text"] == "fast tier"
        fast.chat.completions.create.assert_called_once()
        # Big should still have only the original one call.
        assert big.chat.completions.create.call_count == 1


def test_fast_tier_falls_back_to_default_when_not_configured():
    """If only big tier is configured, metadata.model_tier=fast still uses big."""
    from models.llm_handler import LLMHandler

    big = MagicMock()
    big.chat.completions.create.return_value = _fake_completion(
        "gpt-oss:20b", "big only"
    )

    with patch("openai.OpenAI", return_value=big):
        h = LLMHandler(
            model_path="/tmp",
            model_type="ollama",
            model_name="gpt-oss:20b",
            ollama_base_url="http://big:11434/v1",
        )
        _, env = h.process_event(
            {
                "event_type": "text_query",
                "content": {"text": "hi"},
                "metadata": {"model_tier": "fast"},
            }
        )
        assert env["meta"]["tier"] == "default"
        big.chat.completions.create.assert_called_once()
