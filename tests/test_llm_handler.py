import pytest

from models.llm_handler import LLMHandler


class TestLLMHandler:
    """Test suite for LLMHandler class"""

    def test_init(self):
        handler = LLMHandler(
            model_path="models/llm_model",
            model_type="mock",
            model_name="test/model",
        )
        assert handler.model_path == "models/llm_model"
        assert handler.model_type == "mock"
        assert handler.model_name == "test/model"
        assert isinstance(handler.events_store, dict)

    def test_format_prompt(self):
        handler = LLMHandler(model_path="models/llm_model", model_type="mock")
        text_prompt = handler._format_prompt("text_query", {"text": "Hello world"})
        assert text_prompt == "Query: Hello world\nResponse:"
        code_prompt = handler._format_prompt(
            "code_generation",
            {"description": "Print hello world", "language": "python"},
        )
        assert code_prompt == (
            "Generate code for: Print hello world\nLanguage: python\nCode:"
        )
        default_prompt = handler._format_prompt("unknown_type", {"data": "test"})
        assert "Process this event:" in default_prompt
        assert "test" in default_prompt

    def test_mock_response_envelope(self):
        handler = LLMHandler(model_path="models/llm_model", model_type="mock")
        event_data = {
            "event_type": "text_query",
            "content": {"text": "Hello"},
            "source": "unit",
        }
        event_id, env = handler.process_event(event_data)
        assert env["result"]["text"].startswith("Mock response for query: Hello")
        assert env["meta"]["backend"] == "mock"

        event_data2 = {
            "event_type": "code_generation",
            "content": {"description": "Test function", "language": "python"},
        }
        _, env2 = handler.process_event(event_data2)
        assert "# Mock code for: Test function" in env2["result"]["text"]
        assert "def example_function" in env2["result"]["text"]

        _, env3 = handler.process_event(
            {"event_type": "unknown_type", "content": {}}
        )
        assert "Mock response for event type: unknown_type" in env3["result"]["text"]

    def test_process_event(self):
        handler = LLMHandler(model_path="models/llm_model", model_type="mock")
        event_data = {
            "event_type": "text_query",
            "content": {"text": "Test query"},
            "source": "test",
        }
        event_id, response = handler.process_event(event_data)
        assert isinstance(event_id, str) and len(event_id) > 0
        assert "result" in response
        assert "text" in response["result"]
        assert response["meta"]["backend"] == "mock"
        assert event_id in handler.events_store
        assert handler.events_store[event_id]["status"] == "completed"
        assert handler.events_store[event_id]["event_data"] == event_data

    def test_get_event_status(self):
        handler = LLMHandler(model_path="models/llm_model", model_type="mock")
        event_data = {"event_type": "text_query", "content": {"text": "Test"}}
        event_id, _ = handler.process_event(event_data)
        status = handler.get_event_status(event_id)
        assert status is not None
        assert status["status"] == "completed"
        assert "response" in status
        assert handler.get_event_status("nonexistent") is None
