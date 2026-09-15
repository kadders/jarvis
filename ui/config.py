"""Jarvis chat UI service settings."""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    jarvis_ui_host: str = "0.0.0.0"
    jarvis_ui_port: int = 8003
    # Base URL of the LLM API (scheme + host + port, no /api/v1 suffix).
    jarvis_llm_base_url: str = "http://127.0.0.1:8000"
    jarvis_ui_llm_timeout_s: float = 120.0
    jarvis_ui_history_char_budget: int = 6000
    jarvis_config_dir: Optional[str] = None
    jarvis_config_reload_interval_s: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
