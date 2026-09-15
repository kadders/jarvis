import os
import tempfile
import pytest

from utils.config import get_settings


@pytest.fixture(scope="session", autouse=True)
def _test_env():
    """Fast tests: explicit mock LLM and in-memory idempotency."""
    prev = {}
    keys = {
        "MODEL_TYPE": "mock",
        "IDEMPOTENCY_BACKEND": "memory",
        "JARVIS_OCR_ENGINE": "mock",
        "JARVIS_OCR_DETECTOR": "mock",
    }
    img_dir = tempfile.mkdtemp(prefix="jarvis_test_images_")
    fb_dir = tempfile.mkdtemp(prefix="jarvis_test_feedback_")
    keys["JARVIS_IMAGE_DIR"] = img_dir
    keys["JARVIS_FEEDBACK_DIR"] = fb_dir
    for k, v in keys.items():
        prev[k] = os.environ.get(k)
        os.environ[k] = v
    # Ensure OpenAI auth is off unless a test enables it.
    prev["JARVIS_OPENAI_API_KEY"] = os.environ.get("JARVIS_OPENAI_API_KEY")
    os.environ.pop("JARVIS_OPENAI_API_KEY", None)
    get_settings.cache_clear()
    yield
    for k, old in prev.items():
        if old is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = old
    get_settings.cache_clear()
