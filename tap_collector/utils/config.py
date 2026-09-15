"""Tap collector settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

_ROOT = Path(__file__).resolve().parents[1]

# Default subscribe filters: tap-testing, Duet macros, 4th-combinator CAM sim
DEFAULT_MQTT_TOPICS: list[str] = ["tap/#", "duet/#", "cam/#"]


class Settings(BaseModel):
    api_host: str = "0.0.0.0"
    api_port: int = 8004
    api_debug: bool = False
    environment: str = "development"
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    # Comma-separated MQTT subscribe filters (collector listens; RRF does not consume tap/#|cam/#)
    mqtt_topics: list[str] = list(DEFAULT_MQTT_TOPICS)
    session_store_dir: Path = Path("./cache/tap_sessions")
    # Read-only CAM G-code share (k8s: /cam-gcode). None disables resolution.
    cam_gcode_dir: Path | None = None
    session_idle_timeout_s: float = 30.0
    # Wall-clock max open session length; 0 / unset disables.
    max_session_s: float = 0.0
    live_window_s: float = 1.0
    default_flutes: int = 4
    # Milo LDO spindle default: 400 Hz → 24000 RPM (matches tap-testing)
    default_spindle_freq_hz: float = 400.0
    default_max_rpm: float = 24000.0
    default_min_rpm: float = 9000.0
    jarvis_llm_base_url: str | None = None
    llm_forward_enabled: bool = False
    publish_jarvis_analysis: bool = True
    mqtt_event_buffer: int = 5000
    # Persist MQTT event stream under session_dir/mqtt_events; 0 disables prune (keep forever).
    mqtt_event_retention_days: int = 30
    # Live Modbus → RPM correlation
    modbus_rows_max: int = 5000
    rpm_samples_ws_max: int = 500
    rpm_reanalyze_min_interval_s: float = 1.0
    transition_half_width_s: float = 0.75
    spindle_off_rpm: float = 50.0
    motion_samples_max: int = 10000
    telemetry_log_dir: Path | None = None
    replay_kafka_bootstrap: str | None = None
    replay_kafka_topic: str = "jarvis.telemetry.tap"


def _parse_mqtt_topics(raw: str | None) -> list[str]:
    """Parse comma-separated subscribe filters; default tap/#, duet/#, cam/#."""
    if raw is None or not str(raw).strip():
        return list(DEFAULT_MQTT_TOPICS)
    topics = [t.strip() for t in str(raw).split(",") if t.strip()]
    return topics or list(DEFAULT_MQTT_TOPICS)


@lru_cache
def get_settings() -> Settings:
    store = os.getenv(
        "TAP_COLLECTOR_SESSION_DIR",
        str(_ROOT.parent / "cache" / "tap_sessions"),
    )
    cam_raw = os.getenv("TAP_COLLECTOR_CAM_GCODE_DIR", "").strip()
    cam_dir = Path(cam_raw) if cam_raw else None
    spindle_hz = float(os.getenv("TAP_COLLECTOR_SPINDLE_FREQ_HZ", "400"))
    # max_rpm defaults to spindle_freq * 60 unless explicitly overridden
    max_rpm_env = os.getenv("TAP_COLLECTOR_MAX_RPM")
    max_rpm = float(max_rpm_env) if max_rpm_env is not None else spindle_hz * 60.0
    return Settings(
        api_host=os.getenv("TAP_COLLECTOR_HOST", "0.0.0.0"),
        api_port=int(os.getenv("TAP_COLLECTOR_PORT", "8004")),
        api_debug=os.getenv("TAP_COLLECTOR_DEBUG", "false").lower() in ("true", "1", "t"),
        environment=os.getenv("ENVIRONMENT", "development"),
        mqtt_host=os.getenv("TAP_COLLECTOR_MQTT_HOST", os.getenv("MQTT_HOST", "mosquitto")),
        mqtt_port=int(os.getenv("TAP_COLLECTOR_MQTT_PORT", os.getenv("MQTT_PORT", "1883"))),
        mqtt_username=os.getenv("TAP_COLLECTOR_MQTT_USERNAME") or os.getenv("MQTT_USERNAME") or None,
        mqtt_password=os.getenv("TAP_COLLECTOR_MQTT_PASSWORD") or os.getenv("MQTT_PASSWORD") or None,
        mqtt_topics=_parse_mqtt_topics(os.getenv("TAP_COLLECTOR_MQTT_TOPICS")),
        session_store_dir=Path(store),
        cam_gcode_dir=cam_dir,
        session_idle_timeout_s=float(os.getenv("TAP_COLLECTOR_IDLE_TIMEOUT_S", "30")),
        max_session_s=float(os.getenv("TAP_COLLECTOR_MAX_SESSION_S", "0") or "0"),
        live_window_s=float(os.getenv("TAP_COLLECTOR_LIVE_WINDOW_S", "1.0")),
        default_flutes=int(os.getenv("TAP_COLLECTOR_FLUTES", "4")),
        default_spindle_freq_hz=spindle_hz,
        default_max_rpm=max_rpm,
        default_min_rpm=float(os.getenv("TAP_COLLECTOR_MIN_RPM", "9000")),
        jarvis_llm_base_url=os.getenv("JARVIS_LLM_BASE_URL") or None,
        llm_forward_enabled=os.getenv("TAP_COLLECTOR_LLM_FORWARD", "false").lower()
        in ("true", "1", "t"),
        publish_jarvis_analysis=os.getenv("TAP_COLLECTOR_PUBLISH_ANALYSIS", "true").lower()
        in ("true", "1", "t"),
        mqtt_event_buffer=int(os.getenv("TAP_COLLECTOR_MQTT_EVENT_BUFFER", "5000")),
        mqtt_event_retention_days=int(
            os.getenv("TAP_COLLECTOR_MQTT_EVENT_RETENTION_DAYS", "30")
        ),
        modbus_rows_max=int(os.getenv("TAP_COLLECTOR_MODBUS_ROWS_MAX", "5000")),
        rpm_samples_ws_max=int(os.getenv("TAP_COLLECTOR_RPM_SAMPLES_WS_MAX", "500")),
        rpm_reanalyze_min_interval_s=float(
            os.getenv("TAP_COLLECTOR_RPM_REANALYZE_MIN_INTERVAL_S", "1.0")
        ),
        transition_half_width_s=float(
            os.getenv("TAP_COLLECTOR_TRANSITION_HALF_WIDTH_S", "0.75")
        ),
        spindle_off_rpm=float(os.getenv("TAP_COLLECTOR_SPINDLE_OFF_RPM", "50")),
        motion_samples_max=int(os.getenv("TAP_COLLECTOR_MOTION_SAMPLES_MAX", "10000")),
        telemetry_log_dir=(
            Path(os.getenv("TAP_COLLECTOR_TELEMETRY_LOG_DIR").strip())
            if os.getenv("TAP_COLLECTOR_TELEMETRY_LOG_DIR", "").strip()
            else None
        ),
        replay_kafka_bootstrap=os.getenv("TAP_COLLECTOR_REPLAY_KAFKA_BOOTSTRAP") or None,
        replay_kafka_topic=os.getenv(
            "TAP_COLLECTOR_REPLAY_KAFKA_TOPIC", "jarvis.telemetry.tap"
        ),
    )
