import os
from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables
    """
    # API settings
    api_debug: bool = os.getenv("API_DEBUG", "True").lower() in ["true", "1", "t"]
    api_port: int = int(os.getenv("API_PORT", "8000"))
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_prefix: str = os.getenv("API_PREFIX", "/api/v1")
    
    # LLM model settings
    model_path: str = os.getenv("MODEL_PATH", "models/llm_model")
    model_type: str = os.getenv("MODEL_TYPE", "huggingface")
    model_name: str = os.getenv("MODEL_NAME", "google/flan-t5-base")
    max_length: int = int(os.getenv("MAX_LENGTH", "512"))
    temperature: float = float(os.getenv("TEMPERATURE", "0.7"))
    ollama_base_url: str = os.getenv(
        "OLLAMA_BASE_URL",
        os.getenv("OLLAMA_BASE", "http://localhost:11434/v1"),
    )

    # Optional "fast" Ollama tier (smaller model, e.g. llama3.2:3b for MCP / CPU-friendly traffic).
    # Selected per request via metadata.model_tier == "fast".
    model_name_fast: Optional[str] = os.getenv("MODEL_NAME_FAST") or None
    ollama_fast_base_url: Optional[str] = (
        os.getenv("OLLAMA_FAST_BASE_URL") or None
    )
    # Optional fine-tuned / DPO-served Ollama tag (listed on GET /v1/models).
    model_name_tuned: Optional[str] = os.getenv("MODEL_NAME_TUNED") or None

    # OpenAI-compat (/v1): optional Bearer key for Cursor / LAN clients.
    jarvis_openai_api_key: Optional[str] = os.getenv("JARVIS_OPENAI_API_KEY") or None

    # Cloud fallback when local Ollama fails (Cursor still points at Jarvis /v1).
    # Requires an OpenAI-compatible upstream; cannot call Cursor Pro models directly.
    jarvis_fallback_enabled: bool = os.getenv("JARVIS_FALLBACK_ENABLED", "").lower() in (
        "true",
        "1",
        "t",
    )
    jarvis_fallback_base_url: Optional[str] = os.getenv("JARVIS_FALLBACK_BASE_URL") or None
    jarvis_fallback_api_key: Optional[str] = os.getenv("JARVIS_FALLBACK_API_KEY") or None
    jarvis_fallback_model: str = os.getenv("JARVIS_FALLBACK_MODEL", "gpt-4o-mini")
    jarvis_fallback_timeout_s: float = float(os.getenv("JARVIS_FALLBACK_TIMEOUT_S", "60"))

    # Feedback / preference learning (JSONL on disk or NFS).
    jarvis_feedback_dir: str = os.getenv(
        "JARVIS_FEEDBACK_DIR",
        os.path.join(os.getcwd(), "cache", "llm_feedback"),
    )
    jarvis_feedback_max_inject: int = int(os.getenv("JARVIS_FEEDBACK_MAX_INJECT", "3"))

    # Idempotency (POST /events)
    idempotency_backend: str = os.getenv("IDEMPOTENCY_BACKEND", "memory")
    redis_url: Optional[str] = os.getenv("REDIS_URL") or None
    idempotency_ttl_seconds: int = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "86400"))
    idempotency_key_prefix: str = os.getenv("IDEMPOTENCY_KEY_PREFIX", "jarvis:idempotency:")
    idempotency_max_memory_entries: int = int(os.getenv("IDEMPOTENCY_MAX_MEMORY_ENTRIES", "10000"))
    require_idempotency_key: bool = os.getenv("REQUIRE_IDEMPOTENCY_KEY", "").lower() in ["true", "1", "t"]
    
    # ASR settings
    asr_api_debug: bool = os.getenv("ASR_API_DEBUG", "True").lower() in ["true", "1", "t"]
    asr_api_port: int = int(os.getenv("ASR_API_PORT", "8001"))
    asr_api_host: str = os.getenv("ASR_API_HOST", "0.0.0.0")
    asr_model_path: str = os.getenv("ASR_MODEL_PATH", "models/asr_model")
    asr_model_name: str = os.getenv("ASR_MODEL_NAME", "facebook/wav2vec2-base-960h")
    
    # Audio processing settings
    monitor_audio_dir: bool = os.getenv("MONITOR_AUDIO_DIR", "True").lower() in ["true", "1", "t"]
    audio_input_dir: str = os.getenv("AUDIO_INPUT_DIR", "shared/audio_input")
    audio_output_dir: str = os.getenv("AUDIO_OUTPUT_DIR", "shared/audio_output")
    transcriptions_dir: str = os.getenv("TRANSCRIPTIONS_DIR", "shared/transcriptions")
    
    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_json: bool = os.getenv("LOG_JSON", "").lower() in ("true", "1", "t")

    # OCR / ephemeral images (see docs/services/ocr.md)
    jarvis_image_ttl_s: int = int(os.getenv("JARVIS_IMAGE_TTL_S", "900"))
    jarvis_image_dir: str = os.getenv(
        "JARVIS_IMAGE_DIR", os.path.join(os.getcwd(), "cache", "jarvis_images")
    )
    jarvis_image_max_bytes: int = int(os.getenv("JARVIS_IMAGE_MAX_BYTES", str(8 * 1024 * 1024)))
    jarvis_ocr_models_dir: str = os.getenv(
        "JARVIS_OCR_MODELS_DIR",
        os.path.join(os.getcwd(), "cache", "models", "ocr_ota"),
    )
    jarvis_ocr_engine: str = os.getenv("JARVIS_OCR_ENGINE", "paddle").lower()
    jarvis_ocr_detector: str = os.getenv("JARVIS_OCR_DETECTOR", "onnx").lower()
    jarvis_ocr_device: str = os.getenv("JARVIS_OCR_DEVICE", "cpu")
    jarvis_ocr_max_long_edge: int = int(os.getenv("JARVIS_OCR_MAX_LONG_EDGE", "1280"))
    jarvis_ocr_yolo_onnx_path: str = os.getenv(
        "JARVIS_OCR_YOLO_ONNX_PATH",
        os.path.join(os.getcwd(), "cache", "models", "yolov8n.onnx"),
    )
    jarvis_ocr_llm_enrich: bool = os.getenv("JARVIS_OCR_LLM_ENRICH", "").lower() in (
        "true",
        "1",
        "t",
    )
    jarvis_ocr_relay_allow_insecure: bool = os.getenv(
        "JARVIS_OCR_RELAY_ALLOW_INSECURE", ""
    ).lower() in ("true", "1", "t")
    jarvis_ocr_relay_host_suffix_allowlist: Optional[str] = os.getenv(
        "JARVIS_OCR_RELAY_HOST_SUFFIX_ALLOWLIST"
    )
    jarvis_ocr_relay_timeout_s: float = float(os.getenv("JARVIS_OCR_RELAY_TIMEOUT_S", "8"))
    jarvis_ocr_max_concurrent: int = int(os.getenv("JARVIS_OCR_MAX_CONCURRENT", "4"))
    jarvis_ocr_acquire_timeout_s: float = float(
        os.getenv("JARVIS_OCR_ACQUIRE_TIMEOUT_S", "60")
    )
    jarvis_image_sweep_interval_s: int = int(
        os.getenv("JARVIS_IMAGE_SWEEP_INTERVAL_S", "300")
    )
    jarvis_image_sweep_batch: int = int(os.getenv("JARVIS_IMAGE_SWEEP_BATCH", "200"))
    jarvis_ocr_detector_version: Optional[str] = (
        os.getenv("JARVIS_OCR_DETECTOR_VERSION") or None
    )

    # Hot-reload: poll a directory of files (e.g. Kubernetes projected ConfigMap).
    # Each filename is an env var name; content is the value. See utils/config_watch.py.
    jarvis_config_dir: Optional[str] = os.getenv("JARVIS_CONFIG_DIR") or None
    jarvis_config_reload_interval_s: int = int(
        os.getenv("JARVIS_CONFIG_RELOAD_INTERVAL_S", "30")
    )

    model_config = {
        "env_file": ".env"
    }

@lru_cache()
def get_settings():
    """
    Get cached settings to avoid reloading from environment
    every time settings is accessed
    """
    return Settings()
