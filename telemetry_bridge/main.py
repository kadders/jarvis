"""MQTT → Kafka/Redpanda + session JSONL durable log (PoC bridge)."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None  # type: ignore

try:
    from kafka import KafkaProducer
except ImportError:
    KafkaProducer = None  # type: ignore


def _env_list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [t.strip() for t in raw.split(",") if t.strip()]


class TelemetryBridge:
    def __init__(self) -> None:
        self.mqtt_host = os.environ.get("TELEMETRY_BRIDGE_MQTT_HOST", "mosquitto")
        self.mqtt_port = int(os.environ.get("TELEMETRY_BRIDGE_MQTT_PORT", "1883"))
        self.mqtt_topics = _env_list("TELEMETRY_BRIDGE_MQTT_TOPICS", "tap/#,cam/#,print/#")
        self.mqtt_user = os.environ.get("TELEMETRY_BRIDGE_MQTT_USERNAME") or None
        self.mqtt_pass = os.environ.get("TELEMETRY_BRIDGE_MQTT_PASSWORD") or None
        self.kafka_bootstrap = os.environ.get("TELEMETRY_BRIDGE_KAFKA_BOOTSTRAP", "").strip()
        self.kafka_topic = os.environ.get(
            "TELEMETRY_BRIDGE_KAFKA_TOPIC", "jarvis.telemetry.tap"
        )
        log_dir = os.environ.get("TELEMETRY_BRIDGE_LOG_DIR", "/data/telemetry").strip()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._producer: Any = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _kafka_producer(self) -> Any | None:
        if not self.kafka_bootstrap or KafkaProducer is None:
            return None
        if self._producer is not None:
            return self._producer
        try:
            self._producer = KafkaProducer(
                bootstrap_servers=self.kafka_bootstrap.split(","),
                value_serializer=lambda v: json.dumps(v, separators=(",", ":")).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks=1,
                linger_ms=20,
            )
            logger.info("Kafka producer connected to {}", self.kafka_bootstrap)
        except Exception as e:
            logger.warning("Kafka producer unavailable: {}", e)
            self._producer = None
        return self._producer

    def _append_jsonl(self, session_id: str, leaf: str, envelope: dict[str, Any]) -> None:
        if not session_id:
            return
        sess_dir = self.log_dir / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        path = sess_dir / f"{leaf}.jsonl"
        line = json.dumps(envelope, separators=(",", ":"))
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _handle_message(self, topic: str, payload: bytes) -> None:
        try:
            raw = payload.decode("utf-8")
            data = json.loads(raw)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        parts = topic.split("/")
        if len(parts) < 3:
            return
        root, device_id = parts[0], parts[1]
        leaf = "/".join(parts[2:])
        session_id = str(data.get("session_id") or "")
        envelope: dict[str, Any] = {
            "_topic": topic,
            "_root": root,
            "_device_id": device_id,
            "_leaf": leaf,
            "_ingest_ts": time.time(),
            "session_id": session_id,
            "device_id": device_id,
            "payload": data,
            **{k: v for k, v in data.items() if k != "payload"},
        }
        # Durable JSONL for replay / finalize recovery
        if leaf in ("accel/batch", "motion", "tool", "session", "spindle"):
            file_leaf = {
                "accel/batch": "accel",
                "motion": "motion",
                "tool": "tool",
                "session": "session",
                "spindle": "spindle",
            }[leaf]
            self._append_jsonl(session_id, file_leaf, envelope)
        producer = self._kafka_producer()
        if producer is not None and session_id:
            key = session_id
            try:
                producer.send(self.kafka_topic, key=key, value=envelope)
            except Exception as e:
                logger.debug("Kafka send failed: {}", e)

    def run(self) -> None:
        if mqtt is None:
            logger.error("paho-mqtt not installed")
            sys.exit(1)
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        except (AttributeError, TypeError):
            client = mqtt.Client(protocol=mqtt.MQTTv311)
        if self.mqtt_user:
            client.username_pw_set(self.mqtt_user, self.mqtt_pass)
        client.on_message = lambda _c, _u, msg: self._handle_message(
            msg.topic, msg.payload
        )

        def _on_connect(_c, _u, _f, rc, _props=None):
            if rc != 0:
                logger.error("MQTT connect failed rc={}", rc)
                return
            for topic in self.mqtt_topics:
                client.subscribe(topic, qos=0)
                logger.info("Subscribed {}", topic)

        client.on_connect = _on_connect
        client.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
        client.loop_start()
        logger.info(
            "Telemetry bridge running mqtt={}:{} kafka={} log_dir={}",
            self.mqtt_host,
            self.mqtt_port,
            self.kafka_bootstrap or "disabled",
            self.log_dir,
        )
        while not self._stop.is_set():
            time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
        if self._producer is not None:
            try:
                self._producer.flush(5)
                self._producer.close()
            except Exception:
                pass


def main() -> None:
    bridge = TelemetryBridge()

    def _sig(_signum, _frame):
        bridge._stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    bridge.run()


if __name__ == "__main__":
    main()
