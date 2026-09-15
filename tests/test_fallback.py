"""Cloud fallback when primary Ollama fails."""
from unittest.mock import MagicMock

from models.llm_handler import LLMHandler


def test_chat_falls_back_when_ollama_raises():
    handler = LLMHandler(
        model_path="models/llm_model",
        model_type="ollama",
        model_name="llama3.1:8b",
        ollama_base_url="http://localhost:11434/v1",
        fallback_enabled=True,
        fallback_base_url="https://api.openai.com/v1",
        fallback_api_key="sk-test",
        fallback_model="gpt-4o-mini",
    )
    # Replace clients with mocks.
    boom = MagicMock()
    boom.chat.completions.create.side_effect = RuntimeError("ollama down")
    handler._ollama_client = boom

    ok = MagicMock()
    choice = MagicMock()
    choice.message.content = "fallback hello"
    choice.finish_reason = "stop"
    completion = MagicMock()
    completion.choices = [choice]
    completion.model = "gpt-4o-mini"
    completion.usage = None
    ok.chat.completions.create.return_value = completion
    handler._fallback_client = ok

    result = handler.chat_completion(
        [{"role": "user", "content": "hi"}],
        model="llama3.1:8b",
        stream=False,
    )
    assert result["choices"][0]["message"]["content"] == "fallback hello"
    assert result["jarvis"]["backend"] == "fallback"
    assert result["jarvis"]["fallback"] is True
    ok.chat.completions.create.assert_called_once()


def test_explicit_fallback_model_routes_to_cloud():
    handler = LLMHandler(
        model_path="models/llm_model",
        model_type="mock",
        model_name="mock_model",
        fallback_enabled=True,
        fallback_base_url="https://api.openai.com/v1",
        fallback_api_key="sk-test",
        fallback_model="gpt-4o-mini",
    )
    ok = MagicMock()
    choice = MagicMock()
    choice.message.content = "direct"
    choice.finish_reason = "stop"
    completion = MagicMock()
    completion.choices = [choice]
    completion.model = "gpt-4o-mini"
    completion.usage = None
    ok.chat.completions.create.return_value = completion
    handler._fallback_client = ok

    result = handler.chat_completion(
        [{"role": "user", "content": "hi"}],
        model="gpt-4o-mini",
        stream=False,
    )
    assert result["choices"][0]["message"]["content"] == "direct"
    assert result["jarvis"]["backend"] == "fallback"
