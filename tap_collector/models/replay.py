"""Rebuild session telemetry from durable log (Kafka or bridge JSONL)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

import numpy as np

from models.motion import motion_sample_from_payload

logger = logging.getLogger(__name__)


def _iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _row_sort_key(row: dict[str, Any]) -> tuple[float, int, str]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else None
    t_s = row.get("t_s")
    if t_s is None and payload is not None:
        t_s = payload.get("t0_s")
    if t_s is None:
        t_s = row.get("t0_s") or 0
    seq = row.get("seq")
    if seq is None and payload is not None:
        seq = payload.get("seq")
    try:
        seq_i = int(seq) if seq is not None else 0
    except (TypeError, ValueError):
        seq_i = 0
    return (float(t_s or 0), seq_i, str(row.get("_topic") or ""))


def batch_payload_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize bridge JSONL row to an accel/batch dict."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else None
    if payload and ("ax" in payload or "t0_s" in payload):
        out = dict(payload)
    elif "ax" in row or "t0_s" in row:
        out = {
            k: row[k]
            for k in ("session_id", "seq", "t0_s", "dt_s", "ax", "ay", "az", "device_id")
            if k in row
        }
    elif isinstance(payload, dict):
        out = dict(payload)
    else:
        return None
    if "session_id" not in out and row.get("session_id"):
        out["session_id"] = row["session_id"]
    if "device_id" not in out and (row.get("device_id") or row.get("_device_id")):
        out["device_id"] = row.get("device_id") or row.get("_device_id")
    return out


def peek_accel_jsonl_meta(path: Path) -> dict[str, Any] | None:
    """Return metadata from the first accel batch line (streaming)."""
    for row in _iter_jsonl(path):
        payload = batch_payload_from_row(row)
        if not payload or "t0_s" not in payload:
            continue
        return {
            "session_id": str(payload.get("session_id") or row.get("session_id") or ""),
            "t0_s": float(payload.get("t0_s") or 0.0),
            "seq": int(payload.get("seq") or 0),
            "dt_s": float(payload.get("dt_s") or 0.0),
            "device_id": str(
                payload.get("device_id")
                or row.get("device_id")
                or row.get("_device_id")
                or ""
            ),
            "ingest_ts": row.get("_ingest_ts"),
        }
    return None


def reassemble_accel_jsonl(
    path: Path,
    *,
    sample_rate_hz: float = 800.0,
    session_id: str | None = None,
) -> tuple[np.ndarray, np.ndarray, float, int]:
    """
    Stream ``accel.jsonl`` into contiguous timeseries arrays.

    Returns ``(t, data[3,N], sample_rate_hz, n_batches)``. Does not keep the
    full batch dicts in memory — only axis chunks until a final concatenate.
    """
    t_parts: list[np.ndarray] = []
    ax_parts: list[np.ndarray] = []
    ay_parts: list[np.ndarray] = []
    az_parts: list[np.ndarray] = []
    seen_seq: set[int] = set()
    n_batches = 0
    for row in _iter_jsonl(path):
        payload = batch_payload_from_row(row)
        if not isinstance(payload, dict):
            continue
        sid = str(payload.get("session_id") or row.get("session_id") or "")
        if session_id and sid and sid != session_id:
            continue
        try:
            seq = int(payload.get("seq", n_batches))
        except (TypeError, ValueError):
            seq = n_batches
        if seq in seen_seq:
            continue
        seen_seq.add(seq)
        ax = np.asarray(payload.get("ax") or [], dtype=np.float64)
        ay = np.asarray(payload.get("ay") or [], dtype=np.float64)
        az = np.asarray(payload.get("az") or [], dtype=np.float64)
        n = min(len(ax), len(ay), len(az))
        if n == 0:
            continue
        t0 = float(payload.get("t0_s", 0.0))
        dt = float(payload.get("dt_s") or 0.0)
        if dt <= 0 and sample_rate_hz > 0:
            dt = 1.0 / sample_rate_hz
        t_parts.append(t0 + np.arange(n, dtype=np.float64) * dt)
        ax_parts.append(ax[:n])
        ay_parts.append(ay[:n])
        az_parts.append(az[:n])
        n_batches += 1
    if not t_parts:
        return np.asarray([], dtype=np.float64), np.zeros((3, 0)), float(sample_rate_hz), 0
    t = np.concatenate(t_parts)
    data = np.vstack(
        [
            np.concatenate(ax_parts),
            np.concatenate(ay_parts),
            np.concatenate(az_parts),
        ]
    )
    sr = float(sample_rate_hz)
    if sr <= 0 and len(t) > 1:
        dt_med = float(np.median(np.diff(t)))
        if dt_med > 0:
            sr = 1.0 / dt_med
    return t, data, sr, n_batches


def iter_session_log_events(
    log_dir: Path,
    session_id: str,
    *,
    leaf: str | None = None,
) -> list[dict[str, Any]]:
    """Read bridge JSONL rows for one session (optionally filtered by MQTT leaf)."""
    base = log_dir / session_id
    if not base.is_dir():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.jsonl")):
        if leaf and not path.name.startswith(f"{leaf}."):
            continue
        for row in _iter_jsonl(path):
            if str(row.get("session_id") or "") != session_id:
                continue
            events.append(row)
    events.sort(key=_row_sort_key)
    return events


def replay_motion_from_log(
    log_dir: Path,
    session_id: str,
    append_fn: Callable[[dict[str, Any]], None],
) -> int:
    """Apply motion samples from durable log; returns count added."""
    added = 0
    for row in iter_session_log_events(log_dir, session_id, leaf="motion"):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        sample = motion_sample_from_payload(payload)
        if sample is None:
            continue
        append_fn(sample)
        added += 1
    return added


def tool_events_from_log(
    log_dir: Path,
    session_id: str,
) -> list[dict[str, Any]]:
    """Extract tool event payloads from durable ``tool.jsonl``."""
    events: list[dict[str, Any]] = []
    for row in iter_session_log_events(log_dir, session_id, leaf="tool"):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        if not isinstance(payload, dict):
            continue
        if str(payload.get("session_id") or row.get("session_id") or "") != session_id:
            continue
        events.append(dict(payload))
    return events


def tool_events_from_mqtt_day_files(
    mqtt_events_dir: Path,
    session_id: str,
) -> list[dict[str, Any]]:
    """Scan daily mqtt_events JSONL for ``*/tool`` payloads matching session_id."""
    if not mqtt_events_dir.is_dir():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(mqtt_events_dir.glob("????-??-??.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or session_id not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    topic = str(row.get("topic") or "")
                    if "/tool" not in topic:
                        continue
                    payload = row.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if str(payload.get("session_id") or "") != session_id:
                        continue
                    events.append(dict(payload))
        except OSError:
            continue
    return events


def spindle_payloads_from_log(
    log_dir: Path,
    session_id: str,
) -> list[dict[str, Any]]:
    """Extract spindle MQTT payloads from durable ``spindle.jsonl``."""
    payloads: list[dict[str, Any]] = []
    for row in iter_session_log_events(log_dir, session_id, leaf="spindle"):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        if not isinstance(payload, dict):
            continue
        if str(payload.get("session_id") or row.get("session_id") or "") != session_id:
            continue
        payloads.append(dict(payload))
    return payloads


def spindle_payloads_from_mqtt_day_files(
    mqtt_events_dir: Path,
    session_id: str,
) -> list[dict[str, Any]]:
    """Scan daily mqtt_events JSONL for ``*/spindle`` payloads matching session_id."""
    if not mqtt_events_dir.is_dir():
        return []
    payloads: list[dict[str, Any]] = []
    for path in sorted(mqtt_events_dir.glob("????-??-??.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or session_id not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    topic = str(row.get("topic") or "")
                    if not topic.endswith("/spindle"):
                        continue
                    payload = row.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if str(payload.get("session_id") or "") != session_id:
                        continue
                    payloads.append(dict(payload))
        except OSError:
            continue
    return payloads


def merge_tool_event_lists(
    *lists: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Dedupe tool events by event_id (preferred) or (t_s, tool_number, event_type)."""
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_keys: set[tuple[Any, ...]] = set()
    for group in lists:
        for ev in group:
            if not isinstance(ev, dict):
                continue
            eid = ev.get("event_id")
            if eid:
                key = str(eid)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
            else:
                key_t = (
                    ev.get("t_s"),
                    ev.get("tool_number"),
                    str(ev.get("event_type") or ev.get("event") or ""),
                    ev.get("seq"),
                )
                if key_t in seen_keys:
                    continue
                seen_keys.add(key_t)
            merged.append(dict(ev))

    def _sort_key(ev: dict[str, Any]) -> tuple[float, int]:
        try:
            t = float(ev["t_s"]) if ev.get("t_s") is not None else float("inf")
        except (TypeError, ValueError):
            t = float("inf")
        try:
            seq = int(ev["seq"]) if ev.get("seq") is not None else 0
        except (TypeError, ValueError):
            seq = 0
        return t, seq

    merged.sort(key=_sort_key)
    return merged


def replay_batches_from_log(
    log_dir: Path,
    session_id: str,
    on_batch: Callable[[dict[str, Any]], None],
) -> int:
    """Replay accel/batch payloads from durable log."""
    added = 0
    for row in iter_session_log_events(log_dir, session_id, leaf="accel"):
        payload = batch_payload_from_row(row)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("session_id") or "") != session_id:
            continue
        on_batch(payload)
        added += 1
    return added


def consume_kafka_session(
    bootstrap: str,
    topic: str,
    session_id: str,
    *,
    timeout_s: float = 5.0,
) -> list[dict[str, Any]]:
    """Best-effort fetch of all Kafka records for one session_id (PoC)."""
    try:
        from kafka import KafkaConsumer  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("kafka-python not installed; skip Kafka replay")
        return []
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap.split(","),
        auto_offset_reset="earliest",
        consumer_timeout_ms=int(max(500, timeout_s * 1000)),
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )
    rows: list[dict[str, Any]] = []
    try:
        for msg in consumer:
            val = msg.value
            if not isinstance(val, dict):
                continue
            payload = val.get("payload") if isinstance(val.get("payload"), dict) else val
            if str(payload.get("session_id") or val.get("session_id") or "") != session_id:
                continue
            rows.append(val)
    finally:
        consumer.close()
    return rows


def consume_kafka_session(
    bootstrap: str,
    topic: str,
    session_id: str,
    *,
    timeout_s: float = 5.0,
) -> list[dict[str, Any]]:
    """Best-effort fetch of all Kafka records for one session_id (PoC)."""
    try:
        from kafka import KafkaConsumer  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("kafka-python not installed; skip Kafka replay")
        return []
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap.split(","),
        auto_offset_reset="earliest",
        consumer_timeout_ms=int(max(500, timeout_s * 1000)),
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )
    rows: list[dict[str, Any]] = []
    try:
        for msg in consumer:
            val = msg.value
            if not isinstance(val, dict):
                continue
            payload = val.get("payload") if isinstance(val.get("payload"), dict) else val
            if str(payload.get("session_id") or val.get("session_id") or "") != session_id:
                continue
            rows.append(val)
    finally:
        consumer.close()
    return rows
