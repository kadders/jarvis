"""tap_stream settings — env and/or mounted ConfigMap/Secret files."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

_SESSION_START = frozenset({"start", "start_session", "session_start"})
_SESSION_STOP = frozenset({"stop", "end_session", "session_stop"})

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class Settings(BaseModel):
    api_host: str = "0.0.0.0"
    api_port: int = 8005
    environment: str = "development"
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    device_id: str = "milo"
    mjpeg_url: str = "http://192.168.86.65:8081/stream"
    youtube_rtmp_url: str = "rtmp://a.rtmp.youtube.com/live2"
    youtube_stream_key: str = ""
    youtube_enabled: bool = False
    fps: float = 15.0
    bitrate: str = "2000k"
    rotate_deg: int = 0
    flip: str = ""
    max_width: int = 720
    overlay_stale_s: float = 0.5
    work_dir: Path = Path("/tmp/tap-stream")
    recordings_dir: Path | None = None
    ffmpeg_path: str = "ffmpeg"
    stop_timeout_s: float = 10.0
    fontfile: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    enabled: bool = True
    config_dir: Path | None = None
    secrets_dir: Path | None = None
    config_poll_s: float = 15.0
    config_fingerprint: str = ""

    @property
    def effective_youtube_key(self) -> str:
        if not self.youtube_enabled:
            return ""
        return self.youtube_stream_key.strip()

    @property
    def youtube_configured(self) -> bool:
        return bool(self.effective_youtube_key)


def parse_rotate_deg(raw: str) -> int:
    s = (raw or "").strip().lower()
    aliases = {"0": 0, "none": 0, "90": 90, "180": 180, "270": 270}
    if s in aliases:
        return aliases[s]
    try:
        deg = int(float(s)) % 360
    except ValueError:
        return 0
    return deg if deg in (0, 90, 180, 270) else 0


def parse_flip(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in ("", "0", "none", "off"):
        return ""
    if s in ("h", "hflip", "horizontal"):
        return "h"
    if s in ("v", "vflip", "vertical"):
        return "v"
    if s in ("hv", "vh", "both"):
        return "hv"
    return ""


def _truthy(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def is_session_start(payload: dict) -> bool:
    et = str(payload.get("event_type") or payload.get("event") or "").strip().lower()
    return et in _SESSION_START


def is_session_stop(payload: dict) -> bool:
    et = str(payload.get("event_type") or payload.get("event") or "").strip().lower()
    return et in _SESSION_STOP


def _read_dir_kv(directory: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if directory is None or not directory.is_dir():
        return out
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name.startswith(".") or name in ("..data",):
            continue
        # Skip kubernetes symlink dirs / nested paths from k8s mounts
        try:
            if path.is_symlink() and not path.resolve().is_file():
                continue
        except OSError:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out[name] = text.rstrip("\n")
    return out


def _first(*vals: str | None, default: str = "") -> str:
    for v in vals:
        if v is not None and str(v).strip() != "":
            return str(v)
    return default


def load_settings(
    *,
    config_dir: Path | None = None,
    secrets_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Settings:
    """Load settings from mounted files, then optional env overrides.

    Env wins when set (local tests / one-off overrides). Missing dirs are fine.
    """
    env = environ if environ is not None else dict(os.environ)
    cfg_dir = config_dir
    if cfg_dir is None:
        raw = env.get("TAP_STREAM_CONFIG_DIR", "").strip()
        cfg_dir = Path(raw) if raw else None
    sec_dir = secrets_dir
    if sec_dir is None:
        raw = env.get("TAP_STREAM_SECRETS_DIR", "").strip()
        sec_dir = Path(raw) if raw else None

    files = _read_dir_kv(cfg_dir)
    files.update(_read_dir_kv(sec_dir))

    def file_or_env(*keys: str, default: str = "") -> str:
        # Mounted ConfigMap/Secret files win when present (avoids k8s Service
        # link env like TAP_STREAM_PORT=tcp://…). Process env fills gaps and
        # local-dev overrides when no file exists for that key.
        for k in keys:
            if k in files and files[k] != "":
                return files[k]
        for k in keys:
            if k in env and env[k] != "":
                return env[k]
        return default

    work = file_or_env("TAP_STREAM_WORK_DIR", default="/tmp/tap-stream")
    recordings_raw = file_or_env("TAP_STREAM_RECORDINGS_DIR", default="").strip()
    recordings_dir = Path(recordings_raw) if recordings_raw else None

    yt_key = file_or_env("TAP_YOUTUBE_STREAM_KEY", default="")
    # Default false: MP4-only unless explicitly enabled.
    yt_enabled_raw = None
    if "TAP_YOUTUBE_ENABLED" in env:
        yt_enabled_raw = env["TAP_YOUTUBE_ENABLED"]
    elif "TAP_YOUTUBE_ENABLED" in files:
        yt_enabled_raw = files["TAP_YOUTUBE_ENABLED"]
    youtube_enabled = _truthy(yt_enabled_raw, default=False)

    poll_raw = file_or_env("TAP_STREAM_CONFIG_POLL_S", default="15")
    try:
        poll_s = float(poll_raw)
    except ValueError:
        poll_s = 15.0

    settings = Settings(
        api_host=file_or_env("TAP_STREAM_HOST", default="0.0.0.0"),
        api_port=int(file_or_env("TAP_STREAM_PORT", default="8005")),
        environment=file_or_env("ENVIRONMENT", default="development"),
        mqtt_host=file_or_env(
            "TAP_STREAM_MQTT_HOST", "MQTT_HOST", default="mosquitto"
        ),
        mqtt_port=int(
            file_or_env("TAP_STREAM_MQTT_PORT", "MQTT_PORT", default="1883")
        ),
        mqtt_username=_first(
            env.get("TAP_STREAM_MQTT_USERNAME"),
            env.get("MQTT_USERNAME"),
            files.get("TAP_STREAM_MQTT_USERNAME"),
            files.get("MQTT_USERNAME"),
            default="",
        )
        or None,
        mqtt_password=_first(
            env.get("TAP_STREAM_MQTT_PASSWORD"),
            env.get("MQTT_PASSWORD"),
            files.get("TAP_STREAM_MQTT_PASSWORD"),
            files.get("MQTT_PASSWORD"),
            default="",
        )
        or None,
        device_id=file_or_env(
            "TAP_STREAM_DEVICE_ID", "TAP_MQTT_DEVICE_ID", default="milo"
        ),
        mjpeg_url=file_or_env(
            "TAP_STREAM_MJPEG_URL", default="http://192.168.86.65:8081/stream"
        ),
        youtube_rtmp_url=file_or_env(
            "TAP_YOUTUBE_RTMP_URL", default="rtmp://a.rtmp.youtube.com/live2"
        ).rstrip("/"),
        youtube_stream_key=yt_key,
        youtube_enabled=youtube_enabled,
        fps=float(file_or_env("TAP_VIDEO_FPS", default="15")),
        bitrate=file_or_env("TAP_VIDEO_BITRATE", default="2000k"),
        rotate_deg=parse_rotate_deg(file_or_env("TAP_VIDEO_ROTATE", default="0")),
        flip=parse_flip(file_or_env("TAP_VIDEO_FLIP", default="")),
        max_width=int(file_or_env("TAP_VIDEO_TEST_MAX_WIDTH", default="720")),
        overlay_stale_s=float(
            file_or_env("TAP_STREAM_OVERLAY_STALE_S", default="0.5")
        ),
        work_dir=Path(work),
        recordings_dir=recordings_dir,
        ffmpeg_path=file_or_env("TAP_STREAM_FFMPEG_PATH", default="ffmpeg"),
        stop_timeout_s=float(
            file_or_env("TAP_STREAM_STOP_TIMEOUT_S", default="10")
        ),
        fontfile=file_or_env(
            "TAP_VIDEO_FONT",
            default="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ),
        enabled=_truthy(
            file_or_env("TAP_STREAM_ENABLED", default="true"), default=True
        ),
        config_dir=cfg_dir,
        secrets_dir=sec_dir,
        config_poll_s=max(1.0, poll_s),
    )
    settings.config_fingerprint = settings_fingerprint(settings)
    return settings


def settings_fingerprint(settings: Settings) -> str:
    """Stable hash of operator-tunable fields (never includes full stream key)."""
    key_present = "1" if settings.youtube_stream_key.strip() else "0"
    parts = [
        settings.device_id,
        settings.mjpeg_url,
        settings.mqtt_host,
        str(settings.mqtt_port),
        str(settings.fps),
        settings.bitrate,
        str(settings.rotate_deg),
        settings.flip,
        str(settings.max_width),
        str(settings.overlay_stale_s),
        str(settings.work_dir),
        str(settings.recordings_dir or ""),
        settings.youtube_rtmp_url,
        str(settings.youtube_enabled).lower(),
        key_present,
        str(settings.enabled).lower(),
        str(settings.config_poll_s),
    ]
    blob = "|".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def get_settings() -> Settings:
    """Load current settings (no process-wide cache — poller reloads freely)."""
    return load_settings()
