"""MQTT subscriber loop for tap_collector."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from models.handler import TapCollectorHandler
from utils.config import DEFAULT_MQTT_TOPICS, Settings


class MqttBridge:
    """
    Background MQTT client using paho-mqtt. Publishes analysis when configured.
    """

    def __init__(self, settings: Settings, handler: TapCollectorHandler) -> None:
        self.settings = settings
        self.handler = handler
        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    def publish(self, topic: str, payload: dict[str, Any], qos: int = 0) -> None:
        if self._client is None:
            return
        try:
            body = json.dumps(payload, separators=(",", ":"))
            self._client.publish(topic, body, qos=qos)
        except Exception as e:
            logger.warning("MQTT publish failed: {}", e)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.handler.set_publisher(self.publish)
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt not installed; MQTT subscriber disabled")
            return

        # paho-mqtt 2.x requires CallbackAPIVersion; 1.x uses positional client_id.
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id="jarvis-tap-collector",
            )
        except (AttributeError, TypeError):
            client = mqtt.Client(client_id="jarvis-tap-collector", protocol=mqtt.MQTTv311)
        if self.settings.mqtt_username:
            client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)

        topics = list(self.settings.mqtt_topics or DEFAULT_MQTT_TOPICS)

        def on_connect(client, userdata, flags, rc, properties=None):
            if rc == 0:
                logger.info(
                    "MQTT connected to {}:{}, subscribe {}",
                    self.settings.mqtt_host,
                    self.settings.mqtt_port,
                    topics,
                )
                for topic in topics:
                    client.subscribe(topic)
            else:
                logger.error("MQTT connect failed rc={}", rc)

        def on_message(client, userdata, msg):
            if self._loop is None:
                return
            asyncio.run_coroutine_threadsafe(
                self.handler.handle_message(msg.topic, msg.payload),
                self._loop,
            )

        client.on_connect = on_connect
        client.on_message = on_message
        self._client = client
        try:
            client.connect(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=60)
            client.loop_start()
        except Exception as e:
            logger.error("MQTT connect error: {}", e)
            self._client = None
            return

        async def idle_loop():
            while True:
                await asyncio.sleep(5.0)
                await self.handler.sweep_idle()

        self._task = asyncio.create_task(idle_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
