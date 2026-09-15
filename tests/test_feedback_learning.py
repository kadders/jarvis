"""Feedback injection into local model prompts."""
from pathlib import Path

from models.llm_handler import LLMHandler
from utils.feedback_store import FeedbackStore


def test_events_inject_corrections_from_feedback(tmp_path: Path):
    store = FeedbackStore(str(tmp_path / "feedback.jsonl"), max_inject=3)
    store.append(
        {
            "prompt": "What is the capital of France?",
            "response": "Berlin",
            "rating": -1,
            "correction": "Paris",
            "source": "mcp",
        }
    )
    handler = LLMHandler(
        model_path="models/llm_model",
        model_type="mock",
        model_name="mock_model",
        feedback_store=store,
    )
    messages, learning = handler._ollama_messages(
        "text_query",
        "Query: What is the capital of France?\nResponse:",
    )
    assert learning is True
    joined = "\n".join(m["content"] for m in messages)
    assert "Paris" in joined
    assert "Berlin" in joined

    event_id, envelope = handler.process_event(
        {
            "event_type": "text_query",
            "content": {"text": "capital of France again"},
            "metadata": {"source": "mcp", "prefer_local": True},
        }
    )
    assert event_id
    # Mock path does not go through _ollama_messages; chat path does.
    chat = handler.chat_completion(
        [{"role": "user", "content": "capital of France please"}],
        stream=False,
    )
    assert chat["jarvis"]["learning_applied"] is True


def test_prefer_local_skips_cloud_fallback():
    handler = LLMHandler(
        model_path="models/llm_model",
        model_type="ollama",
        model_name="llama3.1:8b",
        fallback_enabled=True,
        fallback_base_url="https://api.openai.com/v1",
        fallback_api_key="sk-test",
        fallback_model="gpt-4o-mini",
    )
    assert handler._prefer_local({"metadata": {"source": "mcp"}}) is True
    assert handler._prefer_local({"metadata": {"prefer_local": True}}) is True
    assert handler._prefer_local({"metadata": {"source": "ui"}}) is False
