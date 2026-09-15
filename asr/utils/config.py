import os
from functools import lru_cache
from typing import Optional

from pydantic import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables
    """
    # API settings
    api_debug: bool = os.getenv("ASR_API_DEBUG", "True").lower() in ["true", "1", "t"]
    api_port: int = int(os.getenv("ASR_API_PORT", "8001"))
    api_host: str = os.getenv("ASR_API_HOST", "0.0.0.0")
    api_prefix: str = os.getenv("ASR_API_PREFIX", "/api/v1")
    
    # ASR model settings
    asr_model_path: str = os.getenv("ASR_MODEL_PATH", "models/asr_model")
    asr_model_name: str = os.getenv("ASR_MODEL_NAME", "facebook/wav2vec2-base-960h")
    chunk_length_ms: int = int(os.getenv("ASR_CHUNK_LENGTH_MS", "30000"))  # 30 seconds
    
    # Audio directories
    audio_input_dir: str = os.getenv("AUDIO_INPUT_DIR", "/shared/audio_input")
    audio_output_dir: str = os.getenv("AUDIO_OUTPUT_DIR", "/shared/audio_output")
    transcriptions_dir: str = os.getenv("TRANSCRIPTIONS_DIR", "/shared/transcriptions")
    monitor_audio_dir: bool = os.getenv("MONITOR_AUDIO_DIR", "True").lower() in [
        "true",
        "1",
        "t",
    ]

    # Hot-reload (Kubernetes projected ConfigMap volume); same contract as LLM API.
    jarvis_config_dir: Optional[str] = os.getenv("JARVIS_CONFIG_DIR") or None
    jarvis_config_reload_interval_s: int = int(
        os.getenv("JARVIS_CONFIG_RELOAD_INTERVAL_S", "30")
    )

    # WebSocket streaming: allow binary chunks before stream_open when true
    asr_stream_legacy_bytes: bool = os.getenv(
        "ASR_STREAM_LEGACY_BYTES", "True"
    ).lower() in ["true", "1", "t"]
    polling_interval: int = int(os.getenv("POLLING_INTERVAL", "5"))  # seconds
    
    # Integration with LLM API
    llm_api_url: str = os.getenv("LLM_API_URL", "http://llm_api:8000/api/v1/events")
    forward_to_llm: bool = os.getenv("FORWARD_TO_LLM", "True").lower() in ["true", "1", "t"]
    
    # Audio formats supported
    supported_formats: list = ["wav", "mp3", "ogg", "flac", "m4a"]
    
    # Logging
    log_level: str = os.getenv("ASR_LOG_LEVEL", "INFO")
    
    class Config:
        env_file = ".env"

@lru_cache()
def get_settings():
    """
    Get cached settings to avoid reloading from environment
    every time settings are accessed
    """
    return Settings() 