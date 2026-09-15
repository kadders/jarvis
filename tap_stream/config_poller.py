"""Poll mounted ConfigMap/Secret files and hot-apply settings between sessions."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from utils.config import Settings, load_settings, settings_fingerprint

if TYPE_CHECKING:
    from supervisor import StreamSupervisor


class ConfigPoller:
    def __init__(
        self,
        supervisor: StreamSupervisor,
        *,
        on_reload: Callable[[Settings], None] | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._on_reload = on_reload
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_fp = settings_fingerprint(supervisor.settings)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="tap-stream-config-poll", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thr = self._thread
        if thr is not None and thr.is_alive():
            thr.join(timeout=2.0)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            interval = max(1.0, float(self._supervisor.settings.config_poll_s))
            if self._stop.wait(interval):
                break
            try:
                self._tick()
            except Exception as e:
                logger.warning("config poll failed: {}", e)

    def _tick(self) -> None:
        new = load_settings(
            config_dir=self._supervisor.settings.config_dir,
            secrets_dir=self._supervisor.settings.secrets_dir,
        )
        fp = settings_fingerprint(new)
        if fp == self._last_fp:
            return
        old = self._supervisor.settings
        changed: list[str] = []
        if old.fps != new.fps:
            changed.append(f"fps:{old.fps}->{new.fps}")
        if old.bitrate != new.bitrate:
            changed.append("bitrate")
        if old.max_width != new.max_width:
            changed.append(f"max_width:{old.max_width}->{new.max_width}")
        if old.youtube_enabled != new.youtube_enabled:
            changed.append(f"youtube_enabled:{old.youtube_enabled}->{new.youtube_enabled}")
        if bool(old.youtube_stream_key.strip()) != bool(new.youtube_stream_key.strip()):
            changed.append("youtube_key_present")
        if old.mjpeg_url != new.mjpeg_url:
            changed.append("mjpeg_url")
        if old.device_id != new.device_id or old.mqtt_host != new.mqtt_host:
            changed.append("mqtt_identity")
            logger.warning(
                "MQTT identity changed in config — takes full effect after pod recycle"
            )
        self._supervisor.apply_settings(new)
        self._last_fp = fp
        if self._on_reload is not None:
            self._on_reload(new)
        logger.info(
            "config reloaded fingerprint={} changes={} (applies next session)",
            fp,
            ",".join(changed) or "other",
        )
