from __future__ import annotations

import pytest

from utils.config import get_settings
from utils.ocr.relay import RelayError, validate_relay_target


def test_relay_blocks_loopback_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_OCR_RELAY_ALLOW_INSECURE", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    with pytest.raises(RelayError) as ei:
        validate_relay_target("https://127.0.0.1:8080/cb", settings)
    assert ei.value.code == "blocked_ip"
    get_settings.cache_clear()


def test_relay_allows_loopback_when_insecure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_OCR_RELAY_ALLOW_INSECURE", "1")
    get_settings.cache_clear()
    settings = get_settings()
    validate_relay_target("http://127.0.0.1:8080/cb", settings)
    get_settings.cache_clear()
