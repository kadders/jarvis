"""MQTT bridge: session lifecycle + overlay topics."""

from __future__ import annotations

import asyncio
import json
from typing import Callable

from loguru import logger

from supervisor import StreamSupervisor
from utils.config import Settings, is_session_start, is_session_stop


class MqttBridge:
    def __init__(
        self,
        settings: Settings,
        supervisor: StreamSupervisor,
        *,
        on_message: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self._on_message = on_message
        self._client = None
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._watch_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if not self.settings.enabled:
            logger.warning("TAP_STREAM_ENABLED=false — MQTT bridge not started")
            return
        self._loop = asyncio.get_running_loop()
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt not installed")
            return

        device = self.settings.device_id
        topics = [
            f"tap/{device}/session",
            f"tap/{device}/video_overlay",
            f"tap/{device}/accel/batch",
        ]
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="jarvis-tap-stream")
        except (AttributeError, TypeError):
            client = mqtt.Client(client_id="jarvis-tap-stream", protocol=mqtt.MQTTv311)
        if self.settings.mqtt_username:
            client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)

        def on_connect(_client, _userdata, _flags, rc, properties=None):
            if rc == 0:
                self._connected = True
                logger.info("MQTT connected {}:{}, subscribe {}", self.settings.mqtt_host, self.settings.mqtt_port, topics)
                for topic in topics:
                    _client.subscribe(topic)
            else:
                logger.error("MQTT connect failed rc={}", rc)

        def on_disconnect(_client, _userdata, rc, properties=None):
            self._connected = False
            logger.warning("MQTT disconnected rc={}", rc)

        def on_message(_client, _userdata, msg):
            if self._loop is None:
                return
            asyncio.run_coroutine_threadsafe(self._handle(msg.topic, msg.payload), self._loop)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        self._client = client
        try:
            client.connect(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=60)
            client.loop_start()
        except Exception as e:
            logger.error("MQTT connect error: {}", e)
            self._client = None
            return

        async def watch():
            while True:
                await asyncio.sleep(2.0)
                self.supervisor.watchdog()

        self._watch_task = asyncio.create_task(watch())

    async def _handle(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        prefix = f"tap/{self.settings.device_id}/"
        if not topic.startswith(prefix):
            return
        leaf = topic[len(prefix) :]
        if self._on_message is not None:
            self._on_message(leaf, data)
        if leaf == "session":
            sid = str(data.get("session_id") or "")
            if is_session_start(data) and sid:
                yt = self.supervisor.settings.youtube_configured
                logger.info(
                    "Session start {} — starting ffmpeg ({})",
                    sid,
                    "mp4+youtube" if yt else "mp4-only",
                )
                self.supervisor.start_session(sid)
            elif is_session_stop(data):
                logger.info("Session stop — stopping ffmpeg")
                self.supervisor.stop_session()
            return
        if leaf in ("video_overlay", "accel/batch"):
            self.supervisor.update_hud(leaf, data)

    async def stop(self) -> None:
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        self.supervisor.stop_session()
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False
