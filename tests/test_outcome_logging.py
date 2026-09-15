"""Outcome / trajectory logging for training capture."""
from pathlib import Path
import sys

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


def test_feedback_outcome_prompt_response(client: TestClient):
    fb = client.post(
        "/api/v1/feedback",
        json={
            "prompt": "How do we restart llm-api?",
            "response": "kubectl -n jarvis-llm rollout restart deploy/llm-api",
            "rating": 1,
            "preferred": "kubectl -n jarvis-llm rollout restart deploy/llm-api",
            "source": "cursor",
            "metadata": {
                "kind": "outcome",
                "logic": "Verified via rollout status after restart",
            },
        },
    )
    assert fb.status_code == 200
    assert fb.json()["status"] == "ok"
    stats = client.get("/api/v1/feedback/stats")
    assert stats.json()["count"] >= 1


def test_hook_helpers_skip_feedback_tools(monkeypatch):
    hooks = Path("/media/kad/data/repositories/jarvis/.cursor/hooks")
    sys.path.insert(0, str(hooks))
    import jarvis_log_common as common  # noqa: WPS433
    import jarvis_log_mcp as mcp_hook  # noqa: WPS433

    assert "jarvis_log_outcome" in common.SKIP_MCP_TOOLS
    assert common.tool_basename("MCP: jarvis_ask_local") == "jarvis_ask_local"

    posted = {}

    def fake_post(**kwargs):
        posted.update(kwargs)
        return True, '{"status":"ok"}'

    monkeypatch.setattr(mcp_hook, "post_feedback", fake_post)
    monkeypatch.setattr(
        mcp_hook,
        "read_stdin_json",
        lambda: {
            "tool_name": "jarvis_ask_local",
            "tool_input": {"text": "ping"},
            "result_json": '{"event_id":"e1","response":{"result":{"text":"pong"}}}',
            "duration": 12,
        },
    )
    assert mcp_hook.main() == 0
    assert posted["event_id"] == "e1"
    assert "jarvis_ask_local" in posted["prompt"]
