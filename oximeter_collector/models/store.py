"""Persisted device and session records."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReadingPoint:
    ts: float
    hr: int | None = None
    spo2: int | None = None
    valid: bool = False
    finger: bool = False
    battery: int | None = None


@dataclass
class DeviceRecord:
    device_id: str
    mqtt_prefix: str = ""
    online: bool = False
    ip: str | None = None
    fw: str | None = None
    rssi: int | None = None
    battery: int | None = None
    last_hr: int | None = None
    last_spo2: int | None = None
    last_valid: bool = False
    updated_at: float = field(default_factory=time.time)
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionRecord:
    session_id: str
    device_id: str
    started_at: float
    ended_at: float | None = None
    state: str = "recording"
    samples: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.ended_at is not None:
            d["duration_s"] = max(0.0, self.ended_at - self.started_at)
        else:
            d["duration_s"] = max(0.0, time.time() - self.started_at)
        d["n_samples"] = len(self.samples)
        d["n_valid"] = sum(1 for s in self.samples if s.get("valid"))
        return d


class OximeterStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.devices_dir = self.root / "devices"
        self.sessions_dir = self.root / "sessions"
        self.devices_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.devices: dict[str, DeviceRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self._open_session: dict[str, str] = {}
        self._load_all()

    def _load_all(self) -> None:
        for path in self.devices_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rec = DeviceRecord(**{k: data[k] for k in DeviceRecord.__dataclass_fields__ if k in data})
                if "config" in data:
                    rec.config = data["config"]
                self.devices[rec.device_id] = rec
            except (OSError, json.JSONDecodeError, TypeError, KeyError):
                continue
        for path in self.sessions_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rec = SessionRecord(
                    session_id=data["session_id"],
                    device_id=data["device_id"],
                    started_at=float(data["started_at"]),
                    ended_at=data.get("ended_at"),
                    state=data.get("state", "stopped"),
                    samples=data.get("samples") or [],
                    notes=data.get("notes") or "",
                )
                self.sessions[rec.session_id] = rec
                if rec.state == "recording":
                    self._open_session[rec.device_id] = rec.session_id
            except (OSError, json.JSONDecodeError, TypeError, KeyError):
                continue

    def save_device(self, rec: DeviceRecord) -> None:
        self.devices[rec.device_id] = rec
        path = self.devices_dir / f"{rec.device_id}.json"
        data = rec.to_dict()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def save_session(self, rec: SessionRecord) -> None:
        self.sessions[rec.session_id] = rec
        path = self.sessions_dir / f"{rec.session_id}.json"
        path.write_text(json.dumps(rec.to_dict(), indent=2), encoding="utf-8")

    def get_or_create_device(self, device_id: str) -> DeviceRecord:
        if device_id not in self.devices:
            rec = DeviceRecord(
                device_id=device_id,
                mqtt_prefix=f"oximeter/{device_id}",
            )
            self.save_device(rec)
        return self.devices[device_id]

    def open_session(self, device_id: str) -> SessionRecord:
        existing = self._open_session.get(device_id)
        if existing and existing in self.sessions:
            return self.sessions[existing]
        sid = f"{device_id}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        rec = SessionRecord(session_id=sid, device_id=device_id, started_at=time.time())
        self._open_session[device_id] = sid
        self.save_session(rec)
        return rec

    def close_session(self, device_id: str) -> SessionRecord | None:
        sid = self._open_session.pop(device_id, None)
        if not sid or sid not in self.sessions:
            return None
        rec = self.sessions[sid]
        rec.state = "stopped"
        rec.ended_at = time.time()
        self.save_session(rec)
        return rec

    def append_reading(self, device_id: str, point: ReadingPoint) -> SessionRecord | None:
        sid = self._open_session.get(device_id)
        if not sid:
            return None
        rec = self.sessions.get(sid)
        if not rec or rec.state != "recording":
            return None
        rec.samples.append(
            {
                "ts": point.ts,
                "hr": point.hr,
                "spo2": point.spo2,
                "valid": point.valid,
                "finger": point.finger,
                "battery": point.battery,
            }
        )
        if len(rec.samples) > 50000:
            rec.samples = rec.samples[-40000:]
        self.save_session(rec)
        return rec


def session_analytics(samples: list[dict]) -> dict:
    valid = [s for s in samples if s.get("valid")]
    if not valid:
        return {"n_valid": 0, "n_total": len(samples)}
    hrs = [s["hr"] for s in valid if s.get("hr") is not None and s["hr"] >= 0]
    spo2s = [s["spo2"] for s in valid if s.get("spo2") is not None and s["spo2"] >= 0]

    def _stats(vals: list[int]) -> dict:
        if not vals:
            return {}
        return {
            "min": min(vals),
            "max": max(vals),
            "mean": round(sum(vals) / len(vals), 1),
        }

    return {
        "n_total": len(samples),
        "n_valid": len(valid),
        "hr": _stats(hrs),
        "spo2": _stats(spo2s),
    }
