"""
LLM API: hot-reload mounted ConfigMap keys into process env and app.state.
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI

from models.llm_handler import LLMHandler
from utils.config import get_settings
from utils.feedback_store import FeedbackStore
from utils.idempotency import get_store
from utils.k8s_config_reload import run_mounted_config_poll_loop, scan_config_dir
from utils.logging_setup import configure_logging
from utils.ocr.image_store import ImageStore

# Re-export for tests / callers
__all__ = ["scan_config_dir", "rebind_app_runtime_state", "config_reload_loop"]


def rebind_app_runtime_state(app: FastAPI) -> None:
    """Rebuild stateful singletons from current os.environ via get_settings()."""
    import os

    settings = get_settings()
    feedback_store = FeedbackStore(
        os.path.join(settings.jarvis_feedback_dir, "feedback.jsonl"),
        max_inject=settings.jarvis_feedback_max_inject,
    )
    app.state.feedback_store = feedback_store
    app.state.llm_handler = LLMHandler(
        model_path=settings.model_path,
        model_type=settings.model_type,
        model_name=settings.model_name,
        ollama_base_url=settings.ollama_base_url,
        temperature=settings.temperature,
        model_name_fast=settings.model_name_fast,
        ollama_fast_base_url=settings.ollama_fast_base_url,
        model_name_tuned=settings.model_name_tuned,
        feedback_store=feedback_store,
        fallback_enabled=settings.jarvis_fallback_enabled,
        fallback_base_url=settings.jarvis_fallback_base_url,
        fallback_api_key=settings.jarvis_fallback_api_key,
        fallback_model=settings.jarvis_fallback_model,
        fallback_timeout_s=settings.jarvis_fallback_timeout_s,
    )
    app.state.idempotency_store = get_store(
        settings.idempotency_backend,
        settings.redis_url,
        settings.idempotency_max_memory_entries,
        settings.idempotency_key_prefix,
    )
    app.state.image_store = ImageStore(
        settings.jarvis_image_dir,
        settings.jarvis_image_ttl_s,
    )
    new_lim = max(1, settings.jarvis_ocr_max_concurrent)
    old_lim = getattr(app.state, "_ocr_concurrency_limit", None)
    if old_lim != new_lim:
        app.state.ocr_recognize_semaphore = asyncio.Semaphore(new_lim)
        app.state._ocr_concurrency_limit = new_lim


async def config_reload_loop(app: FastAPI) -> None:
    await run_mounted_config_poll_loop(
        app,
        get_settings=get_settings,
        clear_settings_cache=get_settings.cache_clear,
        on_rebind=rebind_app_runtime_state,
        log_prefix="llm-api",
        configure_logging_fn=configure_logging,
    )
