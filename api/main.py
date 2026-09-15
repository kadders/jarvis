from contextlib import asynccontextmanager
import asyncio
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from api.middleware.observability import PrometheusHTTPMiddleware, RequestContextMiddleware
from api.middleware.openai_auth import OpenAICompatAuthMiddleware
from api.routers import events, feedback, images, ocr, openai_compat
from models.llm_handler import LLMHandler
from utils.config import get_settings
from utils.config_watch import config_reload_loop
from utils.feedback_store import FeedbackStore
from utils.idempotency import get_store
from utils.logging_setup import configure_logging
from utils.ocr.image_store import ImageStore

# Load environment variables
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info(
        f"Starting LLM API model_type={settings.model_type} "
        f"model_name={settings.model_name}"
    )
    feedback_store = FeedbackStore(
        os.path.join(settings.jarvis_feedback_dir, "feedback.jsonl"),
        max_inject=settings.jarvis_feedback_max_inject,
    )
    app.state.feedback_store = feedback_store
    handler = LLMHandler(
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
    app.state.llm_handler = handler
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
    app.state.ocr_recognize_semaphore = asyncio.Semaphore(
        max(1, settings.jarvis_ocr_max_concurrent)
    )
    app.state._ocr_concurrency_limit = max(1, settings.jarvis_ocr_max_concurrent)
    app.state.config_reload_lock = asyncio.Lock()

    sweep_task: asyncio.Task | None = None

    async def _sweep_images() -> None:
        while True:
            cfg = get_settings()
            if cfg.jarvis_image_sweep_interval_s <= 0:
                await asyncio.sleep(30)
                continue
            await asyncio.sleep(max(1, cfg.jarvis_image_sweep_interval_s))
            try:
                cfg2 = get_settings()
                app.state.image_store.sweep_expired(cfg2.jarvis_image_sweep_batch)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"image store periodic sweep failed: {e}")

    if settings.jarvis_image_sweep_interval_s > 0:
        sweep_task = asyncio.create_task(_sweep_images())

    config_task: asyncio.Task | None = None
    if (settings.jarvis_config_dir or "").strip():
        config_task = asyncio.create_task(config_reload_loop(app))

    try:
        yield
    finally:
        if config_task:
            config_task.cancel()
            try:
                await config_task
            except asyncio.CancelledError:
                pass
        if sweep_task:
            sweep_task.cancel()
            try:
                await sweep_task
            except asyncio.CancelledError:
                pass
        logger.info("Shutting down LLM API")


# Initialize FastAPI app
app = FastAPI(
    title="Jarvis LLM API",
    description="A container-based LLM API for processing events",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, restrict this to specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(PrometheusHTTPMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(OpenAICompatAuthMiddleware)

# Include routers
app.include_router(events.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(images.router, prefix="/api/v1")
app.include_router(ocr.router, prefix="/api/v1")
app.include_router(openai_compat.router, prefix="/v1")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "ok", "message": "Jarvis LLM API is running"}


@app.get("/health")
async def health_check():
    """Detailed health check endpoint"""
    return {
        "status": "ok",
        "api_version": "0.1.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
    }


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint (local / homelab; protect in production)."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_debug,
    )
