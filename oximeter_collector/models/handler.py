"""MQTT ingest and device/session state for oximeter telemetry."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Callable

import httpx
from loguru import logger

from models.mqtt_events import MqttEventStore
from models.store import DeviceRecord, OximeterStore, ReadingPoint, session_analytics
from utils.config import Settings

_TOPIC_RE = re.compile(r"^oximeter/([^/]+)/(readings|status|config/ack|session)$")


class OximeterCollectorHandler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = OximeterStore(settings.store_dir)
        mqtt_dir = settings.store_dir / "mqtt_events"
        self.mqtt_events = MqttEventStore(
            max_events=settings.mqtt_event_buffer,
            store_dir=mqtt_dir,
            retention_days=settings.mqtt_event_retention_days,
        )
        self._publisher: Callable[[str, dict[str, Any], int], None] | None = None
        self._last_valid_ts: dict[str, float] = {}
        self._ws_subscribers: dict[str, list[asyncio.Queue]] = {}

    def default_config_template(self, device_id: str | None = None) -> dict[str, Any]:
        s = self.settings
        did = device_id or "unknown"
        prefix = f"oximeter/{did}"
        return {
            "v": 1,
            "wifi": {"ssid": s.default_wifi_ssid, "pass": s.default_wifi_pass},
            "mqtt": {
                "enabled": s.default_mqtt_enabled,
                "host": s.default_mqtt_host,
                "port": s.default_mqtt_port,
                "user": s.default_mqtt_user,
                "pass": s.default_mqtt_pass,
                "prefix": prefix,
            },
            "ble": {"enabled": True, "name": f"Oximeter-{did[:8]}"},
            "sensor": {"led_current": s.default_led_current},
        }

    def subscribe_device(self, device_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._ws_subscribers.setdefault(device_id, []).append(q)
        return q

    def unsubscribe_device(self, device_id: str, queue: asyncio.Queue) -> None:
        subs = self._ws_subscribers.get(device_id, [])
        if queue in subs:
            subs.remove(queue)

    def _broadcast(self, device_id: str, msg: dict[str, Any]) -> None:
        for q in list(self._ws_subscribers.get(device_id, [])):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def set_publisher(self, publisher: Callable[[str, dict[str, Any], int], None]) -> None:
        self._publisher = publisher

    async def handle_message(self, topic: str, payload: bytes) -> None:
        self.mqtt_events.record(topic, payload)
        text = payload.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Non-JSON MQTT on {}: {}", topic, text[:120])
            return
        if not isinstance(data, dict):
            return

        m = _TOPIC_RE.match(topic)
        if not m:
            return
        device_id, kind = m.group(1), m.group(2)

        if kind == "status":
            await self._handle_status(device_id, data)
        elif kind == "readings":
            await self._handle_reading(device_id, data)
        elif kind == "config/ack":
            await self._handle_config_ack(device_id, data)
        elif kind == "session":
            await self._handle_session(device_id, data)

    async def _handle_status(self, device_id: str, data: dict[str, Any]) -> None:
        rec = self.store.get_or_create_device(device_id)
        rec.online = bool(data.get("online", True))
        rec.ip = data.get("ip") or rec.ip
        rec.fw = data.get("fw") or rec.fw
        rec.rssi = data.get("rssi")
        rec.battery = data.get("battery")
        if "hr" in data:
            rec.last_hr = data.get("hr")
        if "spo2" in data:
            rec.last_spo2 = data.get("spo2")
        rec.last_valid = bool(data.get("valid", rec.last_valid))
        rec.mqtt_prefix = f"oximeter/{device_id}"
        rec.updated_at = time.time()
        self.store.save_device(rec)
        self._broadcast(device_id, {"type": "status", "data": rec.to_dict()})

    async def _handle_reading(self, device_id: str, data: dict[str, Any]) -> None:
        rec = self.store.get_or_create_device(device_id)
        ts = float(data.get("ts", time.time() * 1000)) / 1000.0
        if ts > 1e12:
            ts = ts / 1000.0
        valid = bool(data.get("valid"))
        finger = bool(data.get("finger", valid))
        hr = data.get("hr")
        spo2 = data.get("spo2")
        rec.last_hr = hr if hr is not None and hr >= 0 else rec.last_hr
        rec.last_spo2 = spo2 if spo2 is not None and spo2 >= 0 else rec.last_spo2
        rec.last_valid = valid
        rec.battery = data.get("battery", rec.battery)
        rec.updated_at = time.time()
        self.store.save_device(rec)

        point = ReadingPoint(
            ts=ts,
            hr=hr if isinstance(hr, int) else None,
            spo2=spo2 if isinstance(spo2, int) else None,
            valid=valid,
            finger=finger,
            battery=data.get("battery"),
        )

        if valid:
            self._last_valid_ts[device_id] = ts
            if device_id not in self.store._open_session:
                self.store.open_session(device_id)
            self.store.append_reading(device_id, point)
        else:
            last = self._last_valid_ts.get(device_id)
            open_sid = self.store._open_session.get(device_id)
            if open_sid and last is not None:
                if ts - last > self.settings.session_idle_timeout_s:
                    closed = self.store.close_session(device_id)
                    if closed:
                        self._broadcast(
                            device_id,
                            {"type": "session_closed", "data": closed.to_dict()},
                        )
            elif finger and device_id not in self.store._open_session:
                sess = self.store.open_session(device_id)
                self._broadcast(device_id, {"type": "session_open", "data": sess.to_dict()})
                self.store.append_reading(device_id, point)

        self._broadcast(
            device_id,
            {
                "type": "reading",
                "data": {
                    "ts": ts,
                    "hr": hr,
                    "spo2": spo2,
                    "valid": valid,
                    "finger": finger,
                    "battery": data.get("battery"),
                },
            },
        )

    async def _handle_config_ack(self, device_id: str, data: dict[str, Any]) -> None:
        rec = self.store.get_or_create_device(device_id)
        rec.config["_last_ack"] = data
        rec.config["_last_ack_at"] = time.time()
        rec.updated_at = time.time()
        self.store.save_device(rec)
        self._broadcast(device_id, {"type": "config_ack", "data": data})

    async def _handle_session(self, device_id: str, data: dict[str, Any]) -> None:
        event = str(data.get("event") or data.get("event_type") or "").lower()
        if event in ("start", "start_session"):
            self.store.open_session(device_id)
        elif event in ("stop", "end_session"):
            self.store.close_session(device_id)

    async def sweep_idle(self) -> None:
        self.mqtt_events.maybe_prune()
        now = time.time()
        for device_id, sid in list(self.store._open_session.items()):
            rec = self.store.sessions.get(sid)
            if not rec:
                self.store._open_session.pop(device_id, None)
                continue
            if not rec.samples:
                if now - rec.started_at > self.settings.session_idle_timeout_s:
                    self.store.close_session(device_id)
                continue
            last_ts = float(rec.samples[-1].get("ts", rec.started_at))
            if now - last_ts > self.settings.session_idle_timeout_s:
                self.store.close_session(device_id)

    def list_devices(self) -> list[dict[str, Any]]:
        out = []
        for rec in sorted(self.store.devices.values(), key=lambda d: d.updated_at, reverse=True):
            d = rec.to_dict()
            d["open_session_id"] = self.store._open_session.get(rec.device_id)
            out.append(d)
        return out

    def get_device(self, device_id: str) -> DeviceRecord | None:
        return self.store.devices.get(device_id)

    def list_sessions(self, device_id: str | None = None) -> list[dict[str, Any]]:
        items = list(self.store.sessions.values())
        if device_id:
            items = [s for s in items if s.device_id == device_id]
        items.sort(key=lambda s: s.started_at, reverse=True)
        return [s.to_dict() for s in items]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        rec = self.store.sessions.get(session_id)
        return rec.to_dict() if rec else None

    def session_series(self, session_id: str, max_points: int = 2000) -> dict[str, Any]:
        rec = self.store.sessions.get(session_id)
        if not rec:
            return {"session_id": session_id, "points": []}
        samples = rec.samples
        if len(samples) > max_points:
            step = max(1, len(samples) // max_points)
            samples = samples[::step]
        return {
            "session_id": session_id,
            "device_id": rec.device_id,
            "n_total": len(rec.samples),
            "points": samples,
        }

    def session_analytics(self, session_id: str) -> dict[str, Any]:
        rec = self.store.sessions.get(session_id)
        if not rec:
            return {}
        return session_analytics(rec.samples)

    async def fetch_config_http(self, device_id: str) -> dict[str, Any]:
        rec = self.store.get_or_create_device(device_id)
        if not rec.ip:
            return {"ok": False, "error": "device IP unknown"}
        url = f"http://{rec.ip}/api/config"
        try:
            async with httpx.AsyncClient(timeout=self.settings.device_http_timeout_s) as client:
                r = await client.get(url)
                data = r.json()
                rec.config["snapshot"] = data
                self.store.save_device(rec)
                return {"ok": True, "config": data, "url": url}
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            return {"ok": False, "error": str(e), "url": url}

    def publish_config(self, device_id: str, config: dict[str, Any]) -> dict[str, Any]:
        if not self._publisher:
            return {"ok": False, "error": "mqtt not connected"}
        topic = f"oximeter/{device_id}/cmd/config"
        self._publisher(topic, config, qos=1)
        rec = self.store.get_or_create_device(device_id)
        rec.config["pending"] = config
        rec.updated_at = time.time()
        self.store.save_device(rec)
        return {"ok": True, "topic": topic, "device_id": device_id}

    async def push_config_http(self, device_id: str, config: dict[str, Any]) -> dict[str, Any]:
        rec = self.store.get_or_create_device(device_id)
        if not rec.ip:
            return {"ok": False, "error": "device IP unknown; wait for status message"}
        url = f"http://{rec.ip}/api/config"
        try:
            async with httpx.AsyncClient(timeout=self.settings.device_http_timeout_s) as client:
                r = await client.post(url, json=config)
                body = r.text
                try:
                    parsed = r.json()
                except json.JSONDecodeError:
                    parsed = {"raw": body}
                return {"ok": r.is_success, "status": r.status_code, "body": parsed, "url": url}
        except httpx.HTTPError as e:
            return {"ok": False, "error": str(e), "url": url}
