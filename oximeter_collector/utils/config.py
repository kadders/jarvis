"""Oximeter collector settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MQTT_TOPICS: list[str] = ["oximeter/#"]


class Settings(BaseModel):
    api_host: str = "0.0.0.0"
    api_port: int = 8005
    api_debug: bool = False
    environment: str = "development"
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topics: list[str] = list(DEFAULT_MQTT_TOPICS)
    store_dir: Path = Path("./cache/oximeter")
    session_idle_timeout_s: float = 30.0
    mqtt_event_buffer: int = 5000
    mqtt_event_retention_days: int = 30
    jarvis_llm_base_url: str | None = None
    llm_forward_enabled: bool = False
    device_http_timeout_s: float = 5.0
    default_wifi_ssid: str = ""
    default_wifi_pass: str = ""
    default_mqtt_host: str = "mqtt.jarvis.lan"
    default_mqtt_port: int = 1883
    default_mqtt_user: str = ""
    default_mqtt_pass: str = ""
    default_mqtt_enabled: bool = True
    default_led_current: int = 0x24


def _parse_mqtt_topics(raw: str | None) -> list[str]:
    if raw is None or not str(raw).strip():
        return list(DEFAULT_MQTT_TOPICS)
    topics = [t.strip() for t in str(raw).split(",") if t.strip()]
    return topics or list(DEFAULT_MQTT_TOPICS)


@lru_cache
def get_settings() -> Settings:
    store = os.getenv(
        "OXI_COLLECTOR_STORE_DIR",
        str(_ROOT.parent / "cache" / "oximeter"),
    )
    return Settings(
        api_host=os.getenv("OXI_COLLECTOR_HOST", "0.0.0.0"),
        api_port=int(os.getenv("OXI_COLLECTOR_PORT", "8005")),
        api_debug=os.getenv("OXI_COLLECTOR_DEBUG", "false").lower() in ("true", "1", "t"),
        environment=os.getenv("ENVIRONMENT", "development"),
        mqtt_host=os.getenv("OXI_COLLECTOR_MQTT_HOST", os.getenv("MQTT_HOST", "mosquitto")),
        mqtt_port=int(os.getenv("OXI_COLLECTOR_MQTT_PORT", os.getenv("MQTT_PORT", "1883"))),
        mqtt_username=os.getenv("OXI_COLLECTOR_MQTT_USERNAME")
        or os.getenv("MQTT_USERNAME")
        or None,
        mqtt_password=os.getenv("OXI_COLLECTOR_MQTT_PASSWORD")
        or os.getenv("MQTT_PASSWORD")
        or None,
        mqtt_topics=_parse_mqtt_topics(os.getenv("OXI_COLLECTOR_MQTT_TOPICS")),
        store_dir=Path(store),
        session_idle_timeout_s=float(os.getenv("OXI_COLLECTOR_IDLE_TIMEOUT_S", "30")),
        mqtt_event_buffer=int(os.getenv("OXI_COLLECTOR_MQTT_EVENT_BUFFER", "5000")),
        mqtt_event_retention_days=int(
            os.getenv("OXI_COLLECTOR_MQTT_EVENT_RETENTION_DAYS", "30")
        ),
        jarvis_llm_base_url=os.getenv("JARVIS_LLM_BASE_URL") or None,
        llm_forward_enabled=os.getenv("OXI_COLLECTOR_LLM_FORWARD", "false").lower()
        in ("true", "1", "t"),
        device_http_timeout_s=float(os.getenv("OXI_COLLECTOR_DEVICE_HTTP_TIMEOUT_S", "5")),
        default_wifi_ssid=os.getenv("OXI_COLLECTOR_DEFAULT_WIFI_SSID", ""),
        default_wifi_pass=os.getenv("OXI_COLLECTOR_DEFAULT_WIFI_PASS", ""),
        default_mqtt_host=os.getenv("OXI_COLLECTOR_DEFAULT_MQTT_HOST", "mqtt.jarvis.lan"),
        default_mqtt_port=int(os.getenv("OXI_COLLECTOR_DEFAULT_MQTT_PORT", "1883")),
        default_mqtt_user=os.getenv("OXI_COLLECTOR_DEFAULT_MQTT_USER", ""),
        default_mqtt_pass=os.getenv("OXI_COLLECTOR_DEFAULT_MQTT_PASS", ""),
        default_mqtt_enabled=os.getenv("OXI_COLLECTOR_DEFAULT_MQTT_ENABLED", "true").lower()
        in ("true", "1", "t"),
        default_led_current=int(os.getenv("OXI_COLLECTOR_DEFAULT_LED_CURRENT", "36"), 0),
    )
