"""Session buffer and MQTT message handler for tap telemetry."""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import numpy as np
from loguru import logger

from models.analyze_live import (
    analyze_segment,
    analyze_segments,
    analyze_session_arrays,
    live_spectrum,
)
from models.gcode_source import CamGcodeIndex, resolve_cam_gcode
from models.import_csv import (
    downsample_series,
    load_run_bundle,
    load_timeseries_npz,
    modbus_row_to_rpm_samples,
    save_timeseries_npz,
    spindle_payload_to_samples,
)
from models.mqtt_events import MqttEventStore
from models.motion import motion_sample_from_payload, motion_view_payload
from models.replay import (
    iter_session_log_events,
    merge_tool_event_lists,
    peek_accel_jsonl_meta,
    reassemble_accel_jsonl,
    replay_batches_from_log,
    replay_motion_from_log,
    spindle_payloads_from_log,
    spindle_payloads_from_mqtt_day_files,
    tool_events_from_log,
    tool_events_from_mqtt_day_files,
)
from models.rpm_provenance import (
    COMMANDED_PLOT_SOURCES,
    MEASURED_PLOT_SOURCES,
    is_observed_source,
)
from models.segments import (
    enrich_segments_from_rrf_snapshot,
    is_selection_tool_event,
    new_segment_id,
    normalize_segment,
    resolve_segment_tools,
    seed_segments_from_tool_events,
    validate_segments,
)
from models.transitions import build_transition_windows
from utils.config import Settings

# Session event aliases (legacy event + additive event_type)
_SESSION_START = frozenset({"start", "start_session", "session_start"})
_SESSION_STOP = frozenset({"stop", "end_session", "session_stop"})

# Job enrichment precedence: live tap-testing > CAM sim > Duet macros
_JOB_FILE_SOURCE_RANK = {"tap": 3, "cam": 2, "duet": 1}

# Cached chart payloads for session SPA (avoid full NPZ load on every view).
UI_PREVIEW_SCHEMA_VERSION = 1
UI_PREVIEW_MAX_POINTS = 2500
UI_PREVIEW_MAX_BINS = 256


@dataclass
class SessionState:
    session_id: str
    device_id: str
    mode: str = "tap"
    sample_rate_hz: float = 800.0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    stopped: bool = False
    recording_state: str = "idle"  # idle | recording | stalled | stopped | error
    batches: dict[int, dict[str, Any]] = field(default_factory=dict)
    modbus_rows: list[dict[str, Any]] = field(default_factory=list)
    analysis_by_source: dict[str, dict[str, Any]] = field(default_factory=dict)
    status_events: list[dict[str, Any]] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    tools_snapshot: list[dict[str, Any]] = field(default_factory=list)
    tools_snapshot_history: list[dict[str, Any]] = field(default_factory=list)
    job_file: str | None = None
    job_file_source: str | None = None  # tap | cam | duet — tap > cam > duet
    source_ref: str | None = None
    gcode_sha256: str | None = None
    gcode_resolution_status: str | None = None
    gcode_summary: dict[str, Any] | None = None
    timeseries_path: Path | None = None
    n_samples: int = 0
    import_label: str | None = None
    notes: str | None = None
    origin: str = "mqtt"  # mqtt | import | duet | cam
    segments: list[dict[str, Any]] = field(default_factory=list)
    segment_analysis: list[dict[str, Any]] = field(default_factory=list)
    rpm_samples: list[dict[str, Any]] = field(default_factory=list)
    load_samples: list[dict[str, Any]] = field(default_factory=list)
    transition_windows: list[dict[str, Any]] = field(default_factory=list)
    run_meta: dict[str, Any] = field(default_factory=dict)
    sidecars: dict[str, Any] = field(default_factory=dict)
    t_start: float | None = None
    t_end: float | None = None
    last_sample_at: float | None = None
    last_batch_seq: int | None = None
    last_rpm_reanalyze_at: float = 0.0
    active_tool_number: int | None = None
    active_tool_name: str | None = None
    active_tool_guid: str | None = None
    active_segment_id: str | None = None
    publisher_state: str | None = None
    warnings: list[str] = field(default_factory=list)
    rpm_alignment: str = "unknown"  # ok | misaligned | none | truncated
    seen_tool_event_ids: set[str] = field(default_factory=set)
    seen_session_events: set[str] = field(default_factory=set)
    seen_rpm_keys: set[tuple[Any, ...]] = field(default_factory=set)
    motion_samples: list[dict[str, Any]] = field(default_factory=list)
    latest_motion: dict[str, Any] | None = None
    # Prefer durable accel.jsonl on finalize (collector restart mid-job).
    recover_from_jsonl: bool = False

    def reassembled_from_batches(self) -> tuple[np.ndarray, np.ndarray, float]:
        """Build timeseries from in-memory MQTT batches only (ignore NPZ on disk)."""
        if not self.batches:
            return np.asarray([]), np.zeros((3, 0)), self.sample_rate_hz
        seqs = sorted(self.batches.keys())
        t_parts: list[np.ndarray] = []
        ax_parts: list[np.ndarray] = []
        ay_parts: list[np.ndarray] = []
        az_parts: list[np.ndarray] = []
        for seq in seqs:
            b = self.batches[seq]
            ax = np.asarray(b.get("ax") or [], dtype=float)
            ay = np.asarray(b.get("ay") or [], dtype=float)
            az = np.asarray(b.get("az") or [], dtype=float)
            n = min(len(ax), len(ay), len(az))
            if n == 0:
                continue
            t0 = float(b.get("t0_s", 0.0))
            dt = float(b.get("dt_s") or (1.0 / self.sample_rate_hz if self.sample_rate_hz else 0.0))
            t = t0 + np.arange(n) * dt
            t_parts.append(t)
            ax_parts.append(ax[:n])
            ay_parts.append(ay[:n])
            az_parts.append(az[:n])
        if not t_parts:
            return np.asarray([]), np.zeros((3, 0)), self.sample_rate_hz
        t = np.concatenate(t_parts)
        data = np.vstack(
            [
                np.concatenate(ax_parts),
                np.concatenate(ay_parts),
                np.concatenate(az_parts),
            ]
        )
        sr = self.sample_rate_hz
        if sr <= 0 and len(t) > 1:
            sr = float(1.0 / np.median(np.diff(t)))
        return t, data, sr

    def reassembled(self) -> tuple[np.ndarray, np.ndarray, float]:
        if self.timeseries_path is not None and self.timeseries_path.is_file():
            return load_timeseries_npz(self.timeseries_path)
        return self.reassembled_from_batches()


class TapCollectorHandler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()
        self._publish_fn = None  # optional callable(topic, payload_dict, qos)
        self._tool_lookup: Callable[[int], list[dict[str, Any]]] | None = None
        self._tool_get: Callable[[str], dict[str, Any] | None] | None = None
        self.mqtt_events = MqttEventStore(
            max_events=settings.mqtt_event_buffer,
            store_dir=settings.session_store_dir / "mqtt_events",
            retention_days=settings.mqtt_event_retention_days,
        )
        self.settings.session_store_dir.mkdir(parents=True, exist_ok=True)
        self._cam_index = CamGcodeIndex(settings.cam_gcode_dir)

    def recover_open_sessions_from_logs(self) -> int:
        """
        Register in-memory sessions for telemetry dirs that look open or have an
        incomplete NPZ vs accel.jsonl (collector restart mid-job).

        Does not load full batch history into RAM — finalize streams from jsonl.
        """
        log_dir = self.settings.telemetry_log_dir
        if log_dir is None or not log_dir.is_dir():
            return 0
        recovered = 0
        idle = float(self.settings.session_idle_timeout_s or 30.0)
        live_grace_s = max(300.0, idle * 10.0)
        for path in sorted(log_dir.glob("*/accel.jsonl")):
            if path.stat().st_size <= 0:
                continue
            sid = path.parent.name
            if sid in self.sessions:
                continue
            first = peek_accel_jsonl_meta(path)
            if first is None:
                continue
            incomplete = self._npz_incomplete_vs_jsonl(sid, path, first)
            meta_path = self.settings.session_store_dir / f"{sid}.json"
            stopped_on_disk = False
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    stopped_on_disk = bool(meta.get("stopped")) or str(
                        meta.get("recording_state") or ""
                    ) == "stopped"
                except Exception:
                    stopped_on_disk = False
            age_s = time.time() - path.stat().st_mtime
            still_live = age_s < live_grace_s
            # Only re-attach sessions whose jsonl was written recently (mid-job
            # restart). Incomplete historical NPZs are rebuilt offline / on finalize.
            if not still_live:
                continue
            if stopped_on_disk and not incomplete:
                continue
            device_id = str(first.get("device_id") or "unknown") or "unknown"
            # Prefer disk JSON (tool_events / motion) over a blank SessionState.
            sess = self._hydrate_from_disk(sid)
            if sess is None:
                sess = SessionState(
                    session_id=sid,
                    device_id=device_id,
                    origin="mqtt",
                    recover_from_jsonl=True,
                )
                self.sessions[sid] = sess
            sess.recover_from_jsonl = True
            if not sess.device_id or sess.device_id in ("unknown", "replay"):
                sess.device_id = device_id
            ingest = first.get("ingest_ts")
            if ingest is not None:
                try:
                    sess.started_at = min(float(sess.started_at or ingest), float(ingest))
                except (TypeError, ValueError):
                    pass
            sess.recording_state = "recording" if still_live else "stalled"
            sess.stopped = False
            recovered += 1
            logger.info(
                "Recovered session {} from accel.jsonl (live={} incomplete_npz={} tools={})",
                sid,
                still_live,
                incomplete,
                len(sess.tool_events),
            )
        return recovered

    def _accel_jsonl_path(self, session_id: str) -> Path | None:
        log_dir = self.settings.telemetry_log_dir
        if log_dir is None:
            return None
        path = log_dir / session_id / "accel.jsonl"
        if path.is_file() and path.stat().st_size > 0:
            return path
        return None

    def _npz_incomplete_vs_jsonl(
        self,
        session_id: str,
        jsonl_path: Path,
        first: dict[str, Any] | None = None,
    ) -> bool:
        npz_path = self._npz_path(session_id)
        if not npz_path.is_file():
            return True
        if first is None:
            first = peek_accel_jsonl_meta(jsonl_path)
        if first is None:
            return False
        try:
            t, _data, _sr = load_timeseries_npz(npz_path)
        except Exception:
            return True
        if t.size == 0:
            return True
        # Truncated NPZ after mid-job restart: starts far after first logged batch.
        return float(t[0]) > float(first.get("t0_s") or 0.0) + 60.0

    def _maybe_mark_jsonl_recovery(self, sess: SessionState) -> None:
        path = self._accel_jsonl_path(sess.session_id)
        if path is None:
            return
        first = peek_accel_jsonl_meta(path)
        if first is None:
            return
        sess.recover_from_jsonl = True
        if first.get("device_id") and (
            not sess.device_id or sess.device_id in ("unknown", "replay")
        ):
            sess.device_id = str(first["device_id"])
        ingest = first.get("ingest_ts")
        if ingest is not None:
            try:
                sess.started_at = min(sess.started_at, float(ingest))
            except (TypeError, ValueError):
                pass
        logger.info(
            "Session {} marked for accel.jsonl finalize recovery ({} bytes)",
            sess.session_id,
            path.stat().st_size,
        )

    def _resolve_finalize_arrays(
        self, sess: SessionState
    ) -> tuple[np.ndarray, np.ndarray, float]:
        """Prefer durable accel.jsonl when it spans more of the job than RAM/NPZ."""
        mem_t, mem_data, mem_sr = sess.reassembled_from_batches()
        path = self._accel_jsonl_path(sess.session_id)
        if path is None:
            if sess.timeseries_path and sess.timeseries_path.is_file() and mem_t.size == 0:
                return load_timeseries_npz(sess.timeseries_path)
            return mem_t, mem_data, mem_sr
        try:
            jt, jd, jsr, n_batches = reassemble_accel_jsonl(
                path,
                sample_rate_hz=sess.sample_rate_hz or mem_sr or 800.0,
                session_id=sess.session_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to reassemble accel.jsonl for {}: {}", sess.session_id, e
            )
            return mem_t, mem_data, mem_sr
        if jt.size == 0:
            return mem_t, mem_data, mem_sr
        use_jsonl = (
            sess.recover_from_jsonl
            or mem_t.size == 0
            or float(jt[0]) < float(mem_t[0]) - 0.5
            or jt.size > mem_t.size
        )
        if not use_jsonl and sess.timeseries_path and sess.timeseries_path.is_file():
            try:
                nt, nd, nsr = load_timeseries_npz(sess.timeseries_path)
                if nt.size and float(jt[0]) < float(nt[0]) - 60.0:
                    use_jsonl = True
            except Exception:
                use_jsonl = True
        if use_jsonl:
            self._warn(sess, "rebuilt_from_accel_jsonl")
            logger.info(
                "Finalize {} using accel.jsonl (batches={}, samples={})",
                sess.session_id,
                n_batches,
                int(jt.size),
            )
            return jt, jd, jsr or mem_sr or sess.sample_rate_hz
        return mem_t, mem_data, mem_sr

    def set_publisher(self, publish_fn) -> None:
        self._publish_fn = publish_fn

    def set_tool_library(
        self,
        *,
        lookup_by_number: Callable[[int], list[dict[str, Any]]],
        get_tool: Callable[[str], dict[str, Any] | None],
    ) -> None:
        """Wire Fusion catalog for GUID / flute resolution on segments."""
        self._tool_lookup = lookup_by_number
        self._tool_get = get_tool

    async def handle_message(self, topic: str, payload: bytes | str) -> None:
        # Always record into the live MQTT event stream (summarized) before routing.
        try:
            self.mqtt_events.record(topic, payload)
        except Exception as e:
            logger.debug("MQTT event record failed: {}", e)
        raw = payload if isinstance(payload, str) else payload.decode("utf-8", errors="replace")
        parts = topic.split("/")
        if len(parts) < 3:
            return
        root = parts[0]
        device_id = parts[1]
        leaf = "/".join(parts[2:])

        # tap/# (tap-testing) and cam/# (4th-combinator sim) share payload shapes
        if root in ("tap", "cam"):
            try:
                data = json.loads(raw)
            except Exception as e:
                logger.warning("Invalid JSON on {}: {}", topic, e)
                return
            if not isinstance(data, dict):
                logger.warning("Non-object JSON on {}", topic)
                return
            stream = root  # "tap" | "cam"
            async with self._lock:
                if leaf == "session":
                    await self._on_session(device_id, data, stream=stream)
                elif leaf == "accel/batch":
                    await self._on_batch(device_id, data, stream=stream)
                elif leaf == "analysis":
                    await self._on_analysis(device_id, data)
                elif leaf == "modbus":
                    await self._on_modbus(device_id, data)
                elif leaf == "spindle":
                    await self._on_spindle(device_id, data)
                elif leaf == "tool":
                    await self._on_tool(device_id, data, stream=stream)
                elif leaf == "status":
                    await self._on_status(device_id, data)
                elif leaf == "motion":
                    await self._on_motion(device_id, data, stream=stream)
            return

        if root == "print":
            try:
                data = json.loads(raw)
            except Exception as e:
                logger.warning("Invalid JSON on {}: {}", topic, e)
                return
            if not isinstance(data, dict):
                return
            async with self._lock:
                if leaf == "motion":
                    data.setdefault("source", "moonraker")
                    await self._on_motion(device_id, data, stream="print")
            return

        if root == "duet":
            # message leaf may be free text; others expect JSON object
            data: dict[str, Any]
            try:
                parsed = json.loads(raw)
                data = parsed if isinstance(parsed, dict) else {"value": parsed}
            except Exception:
                if leaf != "message":
                    logger.warning("Invalid JSON on {}: {}", topic, raw[:120])
                    return
                data = {"text": raw, "ts": time.time()}
            async with self._lock:
                if leaf == "tool":
                    await self._on_duet_tool(device_id, data)
                elif leaf == "job":
                    await self._on_duet_job(device_id, data)
                elif leaf == "state":
                    await self._on_duet_state(device_id, data)
                elif leaf == "message":
                    await self._on_duet_message(device_id, data)
                else:
                    logger.debug("Ignoring unknown duet leaf {}", leaf)

    async def _get_or_create(
        self,
        session_id: str,
        device_id: str,
        *,
        origin: str | None = None,
        mode: str | None = None,
    ) -> SessionState:
        sess = self.sessions.get(session_id)
        if sess is None:
            # Mid-job restart: hydrate prior tool_events/motion from disk JSON.
            sess = self._hydrate_from_disk(session_id)
            if sess is None:
                sess = SessionState(
                    session_id=session_id,
                    device_id=device_id,
                    origin=origin or "mqtt",
                    mode=mode or "tap",
                )
                self.sessions[session_id] = sess
            else:
                if origin:
                    sess.origin = origin
                if mode:
                    sess.mode = mode
                if device_id and (
                    not sess.device_id or sess.device_id in ("unknown", "replay")
                ):
                    sess.device_id = device_id
                # Live traffic after hydrate means the job is still open.
                if sess.stopped:
                    sess.stopped = False
                    if sess.recording_state == "stopped":
                        sess.recording_state = "recording"
            # Mid-job collector restart: durable log exists before RAM session.
            self._maybe_mark_jsonl_recovery(sess)
        return sess

    def _active_session_for_device(self, device_id: str) -> SessionState | None:
        """Most recently updated non-stopped session for device (live binding)."""
        candidates = [
            s
            for s in self.sessions.values()
            if s.device_id == device_id and not s.stopped
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.updated_at)

    async def _resolve_duet_session(
        self,
        machine: str,
        data: dict[str, Any],
    ) -> SessionState:
        """
        Bind Duet MQTT events to a session:
        explicit session_id → active live session for machine → create duet-{machine}-{date}.
        """
        sid = data.get("session_id")
        if sid:
            sess = await self._get_or_create(str(sid), machine, origin="duet", mode="duet")
            sess.origin = "duet"
            if sess.mode in ("tap", ""):
                sess.mode = "duet"
            return sess
        active = self._active_session_for_device(machine)
        if active is not None:
            return active
        day = time.strftime("%Y%m%d")
        sid = f"duet-{machine}-{day}"
        sess = await self._get_or_create(sid, machine, origin="duet", mode="duet")
        sess.origin = "duet"
        sess.mode = "duet"
        return sess

    def _apply_job_file(
        self,
        sess: SessionState,
        job_file: Any,
        source: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        """Set job_file when ``source`` ranks at least as high as the current one."""
        if not job_file:
            return False
        new_rank = _JOB_FILE_SOURCE_RANK.get(source, 0)
        cur_rank = _JOB_FILE_SOURCE_RANK.get(sess.job_file_source or "", 0)
        if sess.job_file and cur_rank > new_rank:
            return False
        sess.job_file = str(job_file)
        sess.job_file_source = source
        if data is not None:
            self._maybe_resolve_gcode(sess, data)
        return True

    async def _on_duet_tool(self, machine: str, data: dict[str, Any]) -> None:
        sess = await self._resolve_duet_session(machine, data)
        # Reuse tap tool path with injected session_id
        payload = {**data, "session_id": sess.session_id}
        await self._on_tool(machine, payload, stream="duet")

    async def _on_duet_job(self, machine: str, data: dict[str, Any]) -> None:
        sess = await self._resolve_duet_session(machine, data)
        sess.updated_at = time.time()
        event = str(data.get("event") or "update")
        self._apply_job_file(sess, data.get("job_file"), "duet", data)
        status_row = {
            "source": "duet",
            "leaf": "job",
            "event": event,
            "job_file": data.get("job_file") or sess.job_file,
            "file_position": data.get("file_position"),
            "status": data.get("status"),
            "ts": self._coerce_wall_ts(data),
        }
        sess.status_events.append(status_row)
        if event == "start":
            sess.stopped = False
            sess.started_at = time.time()
        elif event == "stop":
            sess.stopped = True
            await self._finalize_session(sess)
            return
        self._persist(sess)

    async def _on_duet_state(self, machine: str, data: dict[str, Any]) -> None:
        sess = await self._resolve_duet_session(machine, data)
        sess.updated_at = time.time()
        self._apply_job_file(sess, data.get("job_file"), "duet", data)
        # Emit a tool event when current_tool is present and changed
        tool_n = data.get("current_tool")
        if tool_n is None:
            tool_n = data.get("tool_number")
        if tool_n is not None:
            prev = None
            if sess.tool_events:
                prev = sess.tool_events[-1].get("tool_number")
            if prev != tool_n:
                await self._on_tool(
                    machine,
                    {
                        "session_id": sess.session_id,
                        "event": "change" if sess.tool_events else "start",
                        "tool_number": tool_n,
                        "previous_tool": prev,
                        "t_s": data.get("t_s"),
                        "ts": self._coerce_wall_ts(data),
                        "job_file": data.get("job_file") or sess.job_file,
                        "file_position": data.get("file_position"),
                    },
                    stream="duet",
                )
        sess.status_events.append(
            {
                "source": "duet",
                "leaf": "state",
                "status": data.get("status"),
                "current_tool": tool_n,
                "job_file": data.get("job_file") or sess.job_file,
                "file_position": data.get("file_position"),
                "ts": self._coerce_wall_ts(data),
            }
        )
        # Cap status_events growth
        if len(sess.status_events) > 500:
            sess.status_events = sess.status_events[-500:]
        self._persist(sess)

    async def _on_duet_message(self, machine: str, data: dict[str, Any]) -> None:
        sess = await self._resolve_duet_session(machine, data)
        sess.updated_at = time.time()
        sess.status_events.append(
            {
                "source": "duet",
                "leaf": "message",
                "text": data.get("text") or data.get("message") or data,
                "ts": self._coerce_wall_ts(data),
            }
        )
        if len(sess.status_events) > 500:
            sess.status_events = sess.status_events[-500:]
        self._persist(sess)

    def _session_event_kind(self, data: dict[str, Any]) -> str:
        """Return start|stop|tap_detected|other using event + event_type aliases."""
        raw = str(data.get("event_type") or data.get("event") or "start").lower()
        if raw in _SESSION_START or raw == "start":
            return "start"
        if raw in _SESSION_STOP or raw == "stop":
            return "stop"
        if raw == "tap_detected":
            return "tap_detected"
        # Prefer event field when event_type is exotic
        ev = str(data.get("event") or "").lower()
        if ev in _SESSION_START:
            return "start"
        if ev in _SESSION_STOP:
            return "stop"
        if ev == "tap_detected":
            return "tap_detected"
        return "other"

    async def _on_session(
        self, device_id: str, data: dict[str, Any], *, stream: str = "tap"
    ) -> None:
        sid = str(data.get("session_id") or uuid.uuid4())
        kind = self._session_event_kind(data)
        origin = "cam" if stream == "cam" else None
        sess = await self._get_or_create(sid, device_id, origin=origin)
        if stream == "cam":
            sess.origin = "cam"
        sess.updated_at = time.time()
        sess.mode = str(data.get("mode") or sess.mode)
        if data.get("sample_rate_hz") is not None:
            try:
                sr = float(data["sample_rate_hz"])
                # Keep prior rate when publisher sends 0 (CAM sim has no accel)
                if sr > 0:
                    sess.sample_rate_hz = sr
            except (TypeError, ValueError):
                pass
        self._apply_job_file(sess, data.get("job_file"), stream, data)

        # Idempotent lifecycle keys
        idem = f"{kind}:{data.get('ts')}:{data.get('event_type') or data.get('event')}"
        if kind in ("start", "stop") and idem in sess.seen_session_events:
            return
        if kind in ("start", "stop"):
            sess.seen_session_events.add(idem)

        if kind == "start":
            if sess.stopped and sess.batches:
                sess.warnings.append("session_restarted_with_existing_batches")
            sess.stopped = False
            sess.recording_state = "recording"
            sess.started_at = time.time()
            tn = data.get("tool_number")
            try:
                tn_i = int(tn) if tn is not None else None
            except (TypeError, ValueError):
                tn_i = None
            sess.active_tool_number = tn_i
            sess.active_tool_name = data.get("tool_name")
            # Publisher may include machine-copy hash / fallback summary
            if data.get("gcode_sha256"):
                sess.gcode_sha256 = str(data["gcode_sha256"])
            if isinstance(data.get("gcode_summary"), dict) and not sess.gcode_summary:
                sess.gcode_summary = dict(data["gcode_summary"])
            self._maybe_resolve_gcode(sess, data)
            # Open the initial segment at recording t=0 only when a tool is known.
            # Otherwise wait for the first tool_selected (batches still buffer).
            if tn_i is not None and not any(
                s.get("state") == "open" for s in sess.segments
            ):
                self._open_segment(
                    sess,
                    t0=0.0,
                    tool_number=tn_i,
                    tool_name=data.get("tool_name"),
                    tool_snapshot=data.get("tool_snapshot"),
                    offsets=data.get("offsets"),
                    event_id=data.get("event_id"),
                    boundary_source="session_start",
                )
            logger.info("Session start {} device={} mode={}", sid, device_id, sess.mode)
            self._persist(sess)
        elif kind == "stop":
            sess.stopped = True
            sess.recording_state = "stopped"
            self._close_open_segment(
                sess,
                t1=self._latest_t_s(sess),
                event_id=data.get("event_id"),
                analyze=True,
            )
            logger.info("Session stop {}", sid)
            await self._finalize_session(sess)
        elif kind == "tap_detected":
            sess.status_events.append(data)
            self._persist(sess)

    async def _on_batch(
        self, device_id: str, data: dict[str, Any], *, stream: str = "tap"
    ) -> None:
        sid = str(data.get("session_id") or "")
        if not sid:
            return
        origin = "cam" if stream == "cam" else None
        sess = await self._get_or_create(sid, device_id, origin=origin)
        if stream == "cam":
            sess.origin = "cam"
        sess.updated_at = time.time()
        if sess.stopped:
            if "batch_after_stop" not in sess.warnings:
                sess.warnings.append("batch_after_stop")
            # Still accept for recovery but keep stopped
        else:
            sess.recording_state = "recording"
        seq = int(data.get("seq", 0))
        if seq in sess.batches and sess.last_batch_seq is not None and seq <= sess.last_batch_seq:
            if "batch_seq_overwrite" not in sess.warnings:
                sess.warnings.append("batch_seq_overwrite")
        sess.batches[seq] = data
        sess.last_batch_seq = seq
        sess.last_sample_at = time.time()
        # Extend open segment end time from batch window
        t0 = float(data.get("t0_s") or 0.0)
        dt = float(data.get("dt_s") or 0.0)
        n = min(len(data.get("ax") or []), len(data.get("ay") or []), len(data.get("az") or []))
        t_end = t0 + max(0, n - 1) * dt if n else t0
        self._extend_open_segment(sess, t_end=t_end, n_samples_delta=n)
        # Periodic light persist (every 10 batches)
        if seq % 10 == 0:
            self._persist(sess)

    async def _on_analysis(self, device_id: str, data: dict[str, Any]) -> None:
        sid = str(data.get("session_id") or "")
        if not sid:
            return
        sess = await self._get_or_create(sid, device_id)
        sess.updated_at = time.time()
        source = str(data.get("source") or "unknown")
        # Do not overwrite other sources
        if source not in sess.analysis_by_source:
            sess.analysis_by_source[source] = data
            self._persist(sess)
            if source == "pi" and self.settings.llm_forward_enabled:
                await self._forward_llm(sess, data)

    def _warn(self, sess: SessionState, msg: str) -> None:
        if msg not in sess.warnings[-5:]:
            sess.warnings.append(msg)
            if len(sess.warnings) > 40:
                sess.warnings = sess.warnings[-40:]

    def _coerce_wall_ts(self, data: dict[str, Any]) -> float:
        """Use payload ts only when it looks like Unix wall-clock seconds."""
        raw = data.get("ts")
        if raw is None:
            return time.time()
        try:
            ts = float(raw)
        except (TypeError, ValueError):
            return time.time()
        if ts >= 1e9:
            return ts
        return time.time()

    def _validate_modbus_t_s(self, sess: SessionState, data: dict[str, Any]) -> float | None:
        """Require finite, non-negative recording-relative t_s; reject epoch-like values."""
        try:
            t_s = float(data["t_s"])
        except (KeyError, TypeError, ValueError):
            self._warn(sess, "modbus_row_missing_t_s")
            sess.rpm_alignment = "misaligned"
            return None
        if not math.isfinite(t_s) or t_s < 0:
            self._warn(sess, "modbus_t_s_not_finite_or_negative")
            sess.rpm_alignment = "misaligned"
            return None
        # Unix epoch or millisecond epoch — not recording-relative
        if t_s >= 1e9:
            self._warn(sess, "modbus_t_s_looks_like_wall_epoch")
            sess.rpm_alignment = "misaligned"
            return None
        if t_s >= 1e12:
            self._warn(sess, "modbus_t_s_looks_like_millis_epoch")
            sess.rpm_alignment = "misaligned"
            return None
        # Extremely long "recording" times are almost certainly misaligned clocks
        if t_s > 86400.0:
            self._warn(sess, "modbus_t_s_exceeds_24h_recording")
            sess.rpm_alignment = "misaligned"
            return None
        return t_s

    def _append_rpm_samples(self, sess: SessionState, samples: list[dict[str, Any]]) -> int:
        """Deduplicate by (t_s, source, raw_value); keep samples ordered by t_s."""
        added = 0
        for s in samples:
            key = (
                round(float(s.get("t_s", 0.0)), 6),
                str(s.get("source") or ""),
                s.get("raw_value"),
            )
            if key in sess.seen_rpm_keys:
                continue
            sess.seen_rpm_keys.add(key)
            sess.rpm_samples.append(s)
            added += 1
        if added:
            sess.rpm_samples.sort(key=lambda x: float(x.get("t_s") or 0.0))
            # Bound memory similarly to modbus rows
            max_n = max(self.settings.modbus_rows_max * 2, 1000)
            if len(sess.rpm_samples) > max_n:
                sess.rpm_samples = sess.rpm_samples[-max_n:]
                sess.seen_rpm_keys = {
                    (
                        round(float(s.get("t_s", 0.0)), 6),
                        str(s.get("source") or ""),
                        s.get("raw_value"),
                    )
                    for s in sess.rpm_samples
                }
                self._warn(sess, "rpm_samples_truncated")
        return added

    def _rebuild_transition_windows(self, sess: SessionState) -> None:
        sess.transition_windows = build_transition_windows(
            sess.tool_events,
            sess.modbus_rows,
            segments=sess.segments,
            rpm_samples=sess.rpm_samples,
            half_width_s=self.settings.transition_half_width_s,
            spindle_off_rpm=self.settings.spindle_off_rpm,
        )

    def _rpm_telemetry_summary(self, sess: SessionState) -> dict[str, Any]:
        measured = [
            s for s in sess.rpm_samples if str(s.get("source")) in MEASURED_PLOT_SOURCES
        ]
        commanded = [
            s for s in sess.rpm_samples if str(s.get("source")) in COMMANDED_PLOT_SOURCES
        ]
        latest_m = measured[-1] if measured else None
        latest_c = commanded[-1] if commanded else None
        delta = None
        if latest_m and latest_c:
            delta = float(latest_m["rpm"]) - float(latest_c["rpm"])
        alignment = sess.rpm_alignment
        if measured and alignment in ("unknown", "ok"):
            alignment = "ok"
        elif not measured and not commanded and alignment == "unknown":
            alignment = "none"
        latest_load = sess.load_samples[-1] if sess.load_samples else None
        return {
            "n_modbus_rows": len(sess.modbus_rows),
            "n_rpm_samples": len(sess.rpm_samples),
            "n_load_samples": len(sess.load_samples),
            "latest_measured_rpm": latest_m["rpm"] if latest_m else None,
            "latest_commanded_rpm": latest_c["rpm"] if latest_c else None,
            "latest_measured_minus_commanded": delta,
            "latest_load_percent": (latest_load or {}).get("load_percent"),
            "latest_watts": (latest_load or {}).get("watts"),
            "rpm_alignment": alignment,
            "has_aligned_measured_rpm": bool(measured) and alignment == "ok",
            "transition_windows": sess.transition_windows,
            "n_transition_windows": len(sess.transition_windows),
        }

    def _maybe_reanalyze_for_rpm(self, sess: SessionState, *, force: bool = False) -> None:
        if not sess.segments:
            return
        now = time.time()
        min_iv = float(self.settings.rpm_reanalyze_min_interval_s)
        if not force and (now - sess.last_rpm_reanalyze_at) < min_iv:
            return
        sess.last_rpm_reanalyze_at = now
        self._rebuild_transition_windows(sess)
        self._recompute_segment_analysis(sess)

    async def _on_modbus(self, device_id: str, data: dict[str, Any]) -> None:
        sid = str(data.get("session_id") or "")
        if not sid:
            return
        sess = await self._get_or_create(sid, device_id)
        sess.updated_at = time.time()
        t_s = self._validate_modbus_t_s(sess, data)
        if t_s is None:
            self._persist(sess)
            return

        row = dict(data)
        row["t_s"] = t_s
        max_rows = max(100, int(self.settings.modbus_rows_max))
        sess.modbus_rows.append(row)
        if len(sess.modbus_rows) > max_rows:
            sess.modbus_rows = sess.modbus_rows[-max_rows:]
            self._warn(sess, "modbus_rows_truncated")
            if sess.rpm_alignment == "ok":
                sess.rpm_alignment = "truncated"

        samples = modbus_row_to_rpm_samples(row)
        added = self._append_rpm_samples(sess, samples)
        if samples and sess.rpm_alignment in ("unknown", "none"):
            sess.rpm_alignment = "ok"

        self._rebuild_transition_windows(sess)
        if added and not sess.stopped:
            self._maybe_reanalyze_for_rpm(sess, force=False)
        self._persist(sess)

    def _append_motion_sample(self, sess: SessionState, sample: dict[str, Any]) -> None:
        max_n = max(100, int(self.settings.motion_samples_max))
        sess.motion_samples.append(sample)
        sess.latest_motion = sample
        if len(sess.motion_samples) > max_n:
            sess.motion_samples = sess.motion_samples[-max_n:]
            self._warn(sess, "motion_samples_truncated")

    async def _on_motion(
        self, device_id: str, data: dict[str, Any], *, stream: str = "tap"
    ) -> None:
        sid = str(data.get("session_id") or "")
        if not sid:
            return
        origin = "cam" if stream == "cam" else ("print" if stream == "print" else None)
        sess = await self._get_or_create(sid, device_id, origin=origin)
        if stream == "cam":
            sess.origin = "cam"
        elif stream == "print":
            sess.origin = "print"
        sess.updated_at = time.time()
        sample = motion_sample_from_payload(data)
        if sample is None:
            self._persist(sess)
            return
        if stream == "cam" and not sample.get("source"):
            sample["source"] = "cam_sim"
        elif stream == "print" and not sample.get("source"):
            sample["source"] = "moonraker"
        self._append_motion_sample(sess, sample)
        self._persist(sess)

    async def _on_spindle(self, device_id: str, data: dict[str, Any]) -> None:
        sid = str(data.get("session_id") or "")
        if not sid:
            return
        sess = await self._get_or_create(sid, device_id)
        sess.updated_at = time.time()
        t_s = self._validate_modbus_t_s(sess, data)
        if t_s is None:
            self._persist(sess)
            return
        payload = dict(data)
        payload["t_s"] = t_s
        rpm_samples, load_samples = spindle_payload_to_samples(payload)
        added = self._append_rpm_samples(sess, rpm_samples)
        if load_samples:
            self._append_load_samples(sess, load_samples)
        if rpm_samples and sess.rpm_alignment in ("unknown", "none"):
            sess.rpm_alignment = "ok"
        self._rebuild_transition_windows(sess)
        if added and not sess.stopped:
            self._maybe_reanalyze_for_rpm(sess, force=False)
        self._persist(sess)

    def _append_load_samples(self, sess: SessionState, samples: list[dict[str, Any]]) -> int:
        added = 0
        max_n = max(self.settings.modbus_rows_max * 2, 1000)
        for s in samples:
            sess.load_samples.append(s)
            added += 1
        if len(sess.load_samples) > max_n:
            sess.load_samples = sess.load_samples[-max_n:]
        return added

    async def _on_tool(
        self, device_id: str, data: dict[str, Any], *, stream: str = "tap"
    ) -> None:
        """Tool-change / offset timeline event (tap|cam/{device}/tool)."""
        sid = str(data.get("session_id") or "")
        if not sid:
            return
        origin = "cam" if stream == "cam" else ("duet" if stream == "duet" else None)
        sess = await self._get_or_create(sid, device_id, origin=origin)
        if stream == "cam":
            sess.origin = "cam"
        sess.updated_at = time.time()

        event_id = data.get("event_id")
        if event_id and str(event_id) in sess.seen_tool_event_ids:
            return
        if event_id:
            sess.seen_tool_event_ids.add(str(event_id))

        # Preserve full enriched payload (schema v2+) plus legacy root fields
        event = dict(data)
        event.setdefault("event", str(data.get("event") or "change"))
        event["ts"] = self._coerce_wall_ts(data)
        if "rrf_slot" not in event and event.get("tool_number") is not None:
            event["rrf_slot"] = event.get("tool_number")
        sess.tool_events.append(event)

        job_source = stream if stream in _JOB_FILE_SOURCE_RANK else "tap"
        if data.get("job_file"):
            self._apply_job_file(sess, data.get("job_file"), job_source, data)
        if isinstance(data.get("tools"), list):
            # Richer tap tools snapshot replaces any prior snapshot
            sess.tools_snapshot = data["tools"]
            sess.tools_snapshot_history.append(
                {
                    "ts": event.get("ts"),
                    "event_id": event_id,
                    "event_type": data.get("event_type"),
                    "tools": data["tools"],
                }
            )
            if len(sess.tools_snapshot_history) > 20:
                sess.tools_snapshot_history = sess.tools_snapshot_history[-20:]

        et = str(data.get("event_type") or "").lower()
        try:
            t_s = float(data["t_s"]) if data.get("t_s") is not None else self._latest_t_s(sess)
        except (TypeError, ValueError):
            t_s = self._latest_t_s(sess)

        if et == "tool_offset_changed":
            self._apply_offset_to_open_segment(sess, event)
        elif is_selection_tool_event(event):
            tn = data.get("tool_number")
            try:
                tn_i = int(tn) if tn is not None else None
            except (TypeError, ValueError):
                tn_i = None

            open_seg = next((s for s in sess.segments if s.get("state") == "open"), None)
            same_tool = (
                open_seg is not None
                and open_seg.get("tool_number") == tn_i
                and et != "tool_deselected"
            )
            if same_tool:
                # Enrich existing open segment (session_start → tool_selected same Tn)
                if data.get("tool_snapshot"):
                    open_seg["tool_snapshot"] = data["tool_snapshot"]
                if data.get("offsets"):
                    open_seg["offsets"] = data["offsets"]
                if data.get("tool_name"):
                    open_seg["tool_name"] = data["tool_name"]
                if event_id and not open_seg.get("open_event_id"):
                    open_seg["open_event_id"] = event_id
            else:
                if open_seg is not None:
                    self._close_open_segment(
                        sess,
                        t1=t_s,
                        event_id=event_id,
                        analyze=True,
                    )
                if not sess.stopped and et != "tool_deselected" and (
                    tn_i is None or tn_i >= 0
                ):
                    self._open_segment(
                        sess,
                        t0=t_s,
                        tool_number=tn_i,
                        tool_name=data.get("tool_name"),
                        tool_snapshot=data.get("tool_snapshot"),
                        offsets=data.get("offsets"),
                        event_id=event_id,
                        boundary_source="tool_selected",
                    )
            sess.active_tool_number = tn_i
            sess.active_tool_name = data.get("tool_name")
            self._rebuild_transition_windows(sess)
            # Resolve Fusion GUID from RRF Tn ↔ Fusion post-process.number
            if tn_i is not None and self._tool_lookup is not None:
                matches = self._tool_lookup(tn_i)
                if len(matches) == 1:
                    sess.active_tool_guid = matches[0].get("guid")
                elif len(matches) > 1:
                    sess.active_tool_guid = None
                    if "ambiguous_fusion_tool_number" not in sess.warnings:
                        sess.warnings.append("ambiguous_fusion_tool_number")
                else:
                    sess.active_tool_guid = None

        if not sess.stopped:
            sess.recording_state = "recording"
        self._persist(sess)

    async def _on_status(self, device_id: str, data: dict[str, Any]) -> None:
        state = str(data.get("state") or "")
        logger.debug("Status from {}: {}", device_id, state)
        sid = data.get("session_id")
        if not sid:
            return
        sess = self.sessions.get(str(sid))
        if sess is None:
            return
        sess.publisher_state = state
        sess.updated_at = time.time()
        if state == "error":
            sess.recording_state = "error"
            if data.get("last_error"):
                sess.warnings.append(f"publisher_error:{data.get('last_error')}")
        elif state == "recording" and not sess.stopped:
            sess.recording_state = "recording"
        elif state == "idle" and sess.stopped:
            sess.recording_state = "stopped"
        sess.status_events.append(
            {
                "source": "publisher",
                "state": state,
                "ts": self._coerce_wall_ts(data),
                "detail": data,
            }
        )
        if len(sess.status_events) > 500:
            sess.status_events = sess.status_events[-500:]
        self._persist(sess)

    def _latest_t_s(self, sess: SessionState) -> float:
        if sess.t_end is not None:
            return float(sess.t_end)
        if sess.batches:
            last_seq = max(sess.batches.keys())
            b = sess.batches[last_seq]
            t0 = float(b.get("t0_s") or 0.0)
            dt = float(b.get("dt_s") or 0.0)
            n = min(len(b.get("ax") or []), len(b.get("ay") or []), len(b.get("az") or []))
            return t0 + max(0, n - 1) * dt if n else t0
        return 0.0

    def _resolve_tool_fields(
        self,
        tool_number: int | None,
        *,
        tool_name: str | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tool_number": tool_number,
            "rrf_slot": tool_number,
            "tool_name": tool_name,
            "tool_guid": None,
            "flutes": None,
            "diameter": None,
            "diameter_mm": None,
            "reference_rpm": None,
            "reference_rpm_source": None,
            "feed_mm_min": None,
            "chipload_mm": None,
            "cutting_preset_name": None,
            "ambiguous_tools": [],
            "resolution_status": "unresolved",
        }
        if tool_number is None or self._tool_lookup is None:
            return out
        matches = self._tool_lookup(int(tool_number))
        if len(matches) == 1:
            tool = matches[0]
            out["tool_guid"] = tool.get("guid")
            out["tool_name"] = tool.get("description") or tool_name
            out["flutes"] = tool.get("flutes")
            out["diameter"] = tool.get("diameter")
            out["diameter_mm"] = tool.get("diameter_mm")
            out["reference_rpm"] = tool.get("reference_rpm")
            out["reference_rpm_source"] = tool.get("reference_rpm_source")
            out["feed_mm_min"] = tool.get("feed_mm_min")
            out["chipload_mm"] = tool.get("chipload_mm")
            out["cutting_preset_name"] = tool.get("cutting_preset_name")
            out["resolution_status"] = "resolved"
        elif len(matches) > 1:
            out["ambiguous_tools"] = [
                {
                    "guid": m.get("guid"),
                    "description": m.get("description"),
                    "flutes": m.get("flutes"),
                }
                for m in matches
            ]
            out["resolution_status"] = "ambiguous"
        else:
            out["resolution_status"] = "unmatched"
        return out

    def _open_segment(
        self,
        sess: SessionState,
        *,
        t0: float,
        tool_number: int | None,
        tool_name: str | None = None,
        tool_snapshot: dict[str, Any] | None = None,
        offsets: dict[str, Any] | None = None,
        event_id: Any = None,
        boundary_source: str = "tool_selected",
    ) -> dict[str, Any]:
        resolved = self._resolve_tool_fields(tool_number, tool_name=tool_name)
        # Prefer snapshot spindle RPM as reference when present
        if tool_snapshot and tool_snapshot.get("spindle_rpm") and resolved.get("reference_rpm") is None:
            resolved["reference_rpm"] = tool_snapshot.get("spindle_rpm")
            resolved["reference_rpm_source"] = "rrf_spindle"
        offsets_val = offsets
        if offsets_val is None and tool_snapshot and tool_snapshot.get("offsets_mm"):
            offsets_val = {
                "kind": "rrf_tool_axis",
                "unit": "mm",
                "axes": dict(tool_snapshot.get("offsets_mm") or {}),
                "offsets_probed": tool_snapshot.get("offsets_probed"),
            }
        seg = normalize_segment(
            {
                "segment_id": new_segment_id(),
                "t0": float(t0),
                "t1": float(t0),
                "state": "open",
                "tool_number": resolved["tool_number"],
                "rrf_slot": resolved["rrf_slot"],
                "tool_guid": resolved["tool_guid"],
                "tool_name": resolved["tool_name"],
                "flutes": resolved["flutes"],
                "diameter": resolved["diameter"],
                "diameter_mm": resolved["diameter_mm"],
                "reference_rpm": resolved["reference_rpm"],
                "reference_rpm_source": resolved["reference_rpm_source"],
                "feed_mm_min": resolved.get("feed_mm_min"),
                "chipload_mm": resolved.get("chipload_mm"),
                "cutting_preset_name": resolved.get("cutting_preset_name"),
                "ambiguous_tools": resolved["ambiguous_tools"],
                "resolution_status": resolved["resolution_status"],
                "tool_snapshot": tool_snapshot,
                "offsets": offsets_val,
                "n_samples": 0,
                "open_event_id": event_id,
                "boundary_source": boundary_source,
                "manual": False,
            }
        )
        sess.segments.append(seg)
        sess.active_segment_id = seg["segment_id"]
        sess.active_tool_guid = resolved.get("tool_guid")
        return seg

    def _extend_open_segment(
        self,
        sess: SessionState,
        *,
        t_end: float,
        n_samples_delta: int = 0,
    ) -> None:
        for seg in reversed(sess.segments):
            if seg.get("state") == "open":
                seg["t1"] = max(float(seg.get("t1") or 0.0), float(t_end))
                seg["n_samples"] = int(seg.get("n_samples") or 0) + int(n_samples_delta)
                sess.t_end = seg["t1"]
                if sess.t_start is None:
                    sess.t_start = float(seg.get("t0") or 0.0)
                return

    def _apply_offset_to_open_segment(self, sess: SessionState, event: dict[str, Any]) -> None:
        for seg in reversed(sess.segments):
            if seg.get("state") == "open":
                if event.get("offsets"):
                    seg["offsets"] = event["offsets"]
                if event.get("tool_snapshot"):
                    seg["tool_snapshot"] = event["tool_snapshot"]
                warns = list(seg.get("warnings") or [])
                warns.append("offset_updated")
                seg["warnings"] = warns[-10:]
                return

    def _close_open_segment(
        self,
        sess: SessionState,
        *,
        t1: float,
        event_id: Any = None,
        analyze: bool = False,
    ) -> dict[str, Any] | None:
        open_seg = None
        for seg in reversed(sess.segments):
            if seg.get("state") == "open":
                open_seg = seg
                break
        if open_seg is None:
            return None
        open_seg["t1"] = max(float(open_seg.get("t0") or 0.0) + 1e-6, float(t1))
        open_seg["state"] = "closed"
        open_seg["close_event_id"] = event_id
        sess.active_segment_id = None
        if analyze:
            self._analyze_one_segment(sess, open_seg)
        return open_seg

    def _analyze_one_segment(self, sess: SessionState, seg: dict[str, Any]) -> None:
        t, data, sr = sess.reassembled()
        if data.size < 6 or sr <= 0:
            return
        flutes = seg.get("flutes") or self.settings.default_flutes
        try:
            flutes_i = int(flutes)
        except (TypeError, ValueError):
            flutes_i = self.settings.default_flutes
        tool = None
        if seg.get("tool_guid") and self._tool_get is not None:
            tool = self._tool_get(str(seg["tool_guid"]))
        self._rebuild_transition_windows(sess)
        result = analyze_segment(
            t,
            data,
            sr,
            t0=float(seg["t0"]),
            t1=float(seg["t1"]),
            flute_count=flutes_i,
            max_rpm=self.settings.default_max_rpm,
            min_rpm=self.settings.default_min_rpm,
            spindle_freq_hz=self.settings.default_spindle_freq_hz,
            mode=sess.mode,
            rpm_samples=sess.rpm_samples,
            reference_rpm=seg.get("reference_rpm"),
            reference_rpm_source=seg.get("reference_rpm_source"),
            tool_guid=seg.get("tool_guid"),
            tool_number=seg.get("tool_number"),
            tool=tool,
            segment_id=seg.get("segment_id"),
            transition_windows=sess.transition_windows,
        )
        # Upsert into segment_analysis
        sid = seg.get("segment_id")
        replaced = False
        for i, row in enumerate(sess.segment_analysis):
            if row.get("segment_id") == sid:
                sess.segment_analysis[i] = result
                replaced = True
                break
        if not replaced:
            sess.segment_analysis.append(result)

    def _time_bounds(self, t: np.ndarray) -> tuple[float, float]:
        if t.size == 0:
            return 0.0, 0.0
        return float(t[0]), float(t[-1])

    def _apply_tool_resolution(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._tool_lookup is None:
            return [normalize_segment(s) for s in segments]
        return resolve_segment_tools(
            segments,
            lookup_by_number=self._tool_lookup,
            get_tool=self._tool_get,
        )

    def _seed_segments(self, sess: SessionState, t: np.ndarray) -> None:
        """Seed / rebuild auto segments from tool events (preserve manual ones)."""
        t0, t1 = self._time_bounds(t)
        sess.t_start = t0
        sess.t_end = t1
        if not sess.tool_events:
            return
        manual = [s for s in sess.segments if s.get("manual")]
        # Incomplete live segments must not block a full tool-timeline rebuild.
        if manual:
            return
        segs = seed_segments_from_tool_events(sess.tool_events, t_end=t1, t_start=t0)
        segs = enrich_segments_from_rrf_snapshot(segs, sess.tools_snapshot)
        sess.segments = self._apply_tool_resolution(segs)
        sess.active_segment_id = None

    def _motion_jsonl_path(self, session_id: str) -> Path | None:
        log_dir = self.settings.telemetry_log_dir
        if log_dir is None:
            return None
        path = log_dir / session_id / "motion.jsonl"
        if path.is_file() and path.stat().st_size > 0:
            return path
        return None

    def _tool_jsonl_path(self, session_id: str) -> Path | None:
        log_dir = self.settings.telemetry_log_dir
        if log_dir is None:
            return None
        path = log_dir / session_id / "tool.jsonl"
        if path.is_file() and path.stat().st_size > 0:
            return path
        return None

    def _merge_tool_events_from_durable(self, sess: SessionState) -> int:
        """Merge tool timeline from tool.jsonl and/or mqtt_events; return added count."""
        before = len(sess.tool_events)
        extras: list[dict[str, Any]] = []
        log_dir = self.settings.telemetry_log_dir
        if log_dir is not None and self._tool_jsonl_path(sess.session_id) is not None:
            extras.extend(tool_events_from_log(log_dir, sess.session_id))
        mqtt_dir = self.settings.session_store_dir / "mqtt_events"
        # Always allow mqtt fallback (needed for sessions that predate tool.jsonl).
        extras.extend(tool_events_from_mqtt_day_files(mqtt_dir, sess.session_id))
        if not extras and not sess.tool_events:
            return 0
        merged = merge_tool_event_lists(sess.tool_events, extras)
        sess.tool_events = merged
        sess.seen_tool_event_ids = {
            str(ev["event_id"]) for ev in merged if ev.get("event_id")
        }
        # Prefer the richest tools snapshot from recovered events.
        for ev in reversed(merged):
            tools = ev.get("tools")
            if isinstance(tools, list) and tools:
                sess.tools_snapshot = tools
                break
        added = max(0, len(merged) - before)
        if added:
            self._warn(sess, "rebuilt_tool_events_from_durable")
            logger.info(
                "Session {} restored tool_events ({} → {})",
                sess.session_id,
                before,
                len(merged),
            )
        return added

    def _maybe_restore_motion_from_log(
        self, sess: SessionState, *, t_start: float | None
    ) -> int:
        """Replace truncated / late-only motion from durable motion.jsonl when needed."""
        path = self._motion_jsonl_path(sess.session_id)
        if path is None or self.settings.telemetry_log_dir is None:
            return 0

        def _first_motion_t() -> float | None:
            if not sess.motion_samples:
                return None
            try:
                return float(sess.motion_samples[0].get("t_s"))
            except (TypeError, ValueError, AttributeError):
                return None

        first_t = _first_motion_t()
        late_vs_adxl = (
            t_start is not None
            and first_t is not None
            and first_t > float(t_start) + 60.0
        )
        # Count durable rows cheaply (line count); enough to detect truncation.
        try:
            with path.open("r", encoding="utf-8") as fh:
                durable_n = sum(1 for line in fh if line.strip())
        except OSError:
            durable_n = 0
        truncated = "motion_samples_truncated" in sess.warnings
        need = (
            sess.recover_from_jsonl
            or truncated
            or late_vs_adxl
            or (durable_n > 0 and len(sess.motion_samples) < durable_n)
            or (durable_n > 0 and not sess.motion_samples)
        )
        if not need:
            return 0

        sess.motion_samples = []
        sess.latest_motion = None
        # Replay without ring-buffer truncation so early tools stay visible.
        added = 0

        def _append_unbounded(sample: dict[str, Any]) -> None:
            nonlocal added
            sess.motion_samples.append(sample)
            sess.latest_motion = sample
            added += 1

        replay_motion_from_log(
            self.settings.telemetry_log_dir,
            sess.session_id,
            _append_unbounded,
        )
        if added:
            self._warn(sess, "rebuilt_motion_from_jsonl")
            logger.info(
                "Session {} restored motion_samples from jsonl (n={})",
                sess.session_id,
                added,
            )
        return added

    def _spindle_jsonl_path(self, session_id: str) -> Path | None:
        log_dir = self.settings.telemetry_log_dir
        if log_dir is None:
            return None
        path = log_dir / session_id / "spindle.jsonl"
        if path.is_file() and path.stat().st_size > 0:
            return path
        return None

    def _maybe_restore_rpm_from_durable(
        self, sess: SessionState, *, t_start: float | None
    ) -> int:
        """Rebuild rpm/load samples from spindle.jsonl and/or mqtt_events when truncated/late."""
        log_dir = self.settings.telemetry_log_dir
        mqtt_dir = self.settings.session_store_dir / "mqtt_events"
        payloads: list[dict[str, Any]] = []
        if log_dir is not None and self._spindle_jsonl_path(sess.session_id) is not None:
            payloads.extend(spindle_payloads_from_log(log_dir, sess.session_id))
        payloads.extend(spindle_payloads_from_mqtt_day_files(mqtt_dir, sess.session_id))
        if not payloads:
            return 0

        def _first_rpm_t() -> float | None:
            if not sess.rpm_samples:
                return None
            try:
                return float(sess.rpm_samples[0].get("t_s"))
            except (TypeError, ValueError, AttributeError):
                return None

        first_t = _first_rpm_t()
        late_vs_adxl = (
            t_start is not None
            and first_t is not None
            and first_t > float(t_start) + 60.0
        )
        # Each spindle MQTT row typically yields 2 rpm_samples (measured+commanded).
        durable_n = len(payloads)
        truncated = "rpm_samples_truncated" in sess.warnings
        need = (
            sess.recover_from_jsonl
            or truncated
            or late_vs_adxl
            or (durable_n > 0 and not sess.rpm_samples)
            or (durable_n > 0 and len(sess.rpm_samples) < durable_n)
        )
        if not need:
            return 0

        before = len(sess.rpm_samples)
        sess.rpm_samples = []
        sess.load_samples = []
        sess.seen_rpm_keys = set()
        added_rpm = 0
        for payload in payloads:
            rpm_samples, load_samples = spindle_payload_to_samples(payload)
            for s in rpm_samples:
                key = (
                    round(float(s.get("t_s", 0.0)), 6),
                    str(s.get("source") or ""),
                    s.get("raw_value"),
                )
                if key in sess.seen_rpm_keys:
                    continue
                sess.seen_rpm_keys.add(key)
                sess.rpm_samples.append(s)
                added_rpm += 1
            if load_samples:
                sess.load_samples.extend(load_samples)
        sess.rpm_samples.sort(key=lambda x: float(x.get("t_s") or 0.0))
        sess.load_samples.sort(key=lambda x: float(x.get("t_s") or 0.0))
        if sess.rpm_samples and sess.rpm_alignment in ("unknown", "none", "truncated"):
            sess.rpm_alignment = "ok"
        if added_rpm:
            self._warn(sess, "rebuilt_rpm_from_durable")
            logger.info(
                "Session {} restored rpm_samples from durable spindle ({} → {})",
                sess.session_id,
                before,
                len(sess.rpm_samples),
            )
        return added_rpm

    def _recompute_segment_analysis(self, sess: SessionState) -> None:
        t, data, sr = sess.reassembled()
        if data.size == 0 or sr <= 0:
            sess.segment_analysis = []
            return
        t0, t1 = self._time_bounds(t)
        sess.t_start = t0
        sess.t_end = t1
        if not sess.segments:
            sess.segment_analysis = []
            return
        # Ensure flutes filled from defaults when still missing
        for seg in sess.segments:
            if seg.get("flutes") is None:
                seg["flutes"] = self.settings.default_flutes
        self._rebuild_transition_windows(sess)
        sess.segment_analysis = analyze_segments(
            t,
            data,
            sr,
            sess.segments,
            default_flutes=self.settings.default_flutes,
            max_rpm=self.settings.default_max_rpm,
            min_rpm=self.settings.default_min_rpm,
            spindle_freq_hz=self.settings.default_spindle_freq_hz,
            mode=sess.mode,
            rpm_samples=sess.rpm_samples,
            get_tool=self._tool_get,
            transition_windows=sess.transition_windows,
        )

    def _maybe_resolve_gcode(
        self, sess: SessionState, data: dict[str, Any] | None = None
    ) -> None:
        """Resolve job_file against the CAM share; accept publisher summary as fallback."""
        data = data or {}
        sha = data.get("gcode_sha256") or sess.gcode_sha256
        if data.get("gcode_sha256"):
            sess.gcode_sha256 = str(data["gcode_sha256"])
        if isinstance(data.get("gcode_summary"), dict) and (
            not sess.gcode_summary
            or sess.gcode_resolution_status in (None, "missing", "disabled")
        ):
            # Keep publisher fallback until CAM resolves
            pub = dict(data["gcode_summary"])
            pub.setdefault("source", "rrf_download")
            pub.setdefault("gcode_resolution_status", "rrf_download")
            sess.gcode_summary = pub
            if pub.get("gcode_sha256") and not sess.gcode_sha256:
                sess.gcode_sha256 = str(pub["gcode_sha256"])
            if not sess.gcode_resolution_status:
                sess.gcode_resolution_status = "rrf_download"

        if not sess.job_file:
            return
        if sess.gcode_resolution_status == "resolved" and sess.source_ref:
            return
        result = resolve_cam_gcode(
            sess.job_file,
            cam_dir=self.settings.cam_gcode_dir,
            index=self._cam_index,
            gcode_sha256=str(sha) if sha else None,
        )
        sess.gcode_resolution_status = result.status
        if result.candidates is not None and result.status == "ambiguous":
            if "gcode_ambiguous_basename" not in sess.warnings:
                sess.warnings.append("gcode_ambiguous_basename")
        if result.status == "resolved" and result.gcode_summary:
            sess.source_ref = result.source_ref
            sess.gcode_sha256 = result.gcode_sha256 or sess.gcode_sha256
            sess.gcode_summary = result.gcode_summary
        elif result.status == "ambiguous" and result.gcode_summary:
            sess.gcode_summary = result.gcode_summary
        elif (
            result.status in ("missing", "disabled")
            and isinstance(data.get("gcode_summary"), dict)
        ):
            sess.gcode_resolution_status = "rrf_download"
            sess.gcode_summary = dict(data["gcode_summary"])
            sess.gcode_summary.setdefault("source", "rrf_download")
            sess.gcode_summary["gcode_resolution_status"] = "rrf_download"

    async def _finalize_session(self, sess: SessionState) -> None:
        # Ensure any open live segment is closed before seeding/analysis
        self._close_open_segment(
            sess, t1=self._latest_t_s(sess), event_id=None, analyze=False
        )
        # Restore tool timeline + motion before analysis (collector restart recovery).
        self._merge_tool_events_from_durable(sess)
        t, data, sr = self._resolve_finalize_arrays(sess)
        t0 = float(t[0]) if getattr(t, "size", 0) else sess.t_start
        self._maybe_restore_motion_from_log(sess, t_start=t0)
        self._maybe_restore_rpm_from_durable(sess, t_start=t0)
        if data.size > 0:
            sess.n_samples = int(data.shape[1])
            sess.sample_rate_hz = sr or sess.sample_rate_hz
            sess.t_start, sess.t_end = self._time_bounds(t)
            # Persist live ADXL the same way as imports (survives pod restart)
            npz_path = self._npz_path(sess.session_id)
            try:
                save_timeseries_npz(npz_path, t, data, sr or sess.sample_rate_hz)
                sess.timeseries_path = npz_path
            except OSError as e:
                logger.warning(
                    "Failed to write NPZ for {}: {}", sess.session_id, e
                )
        sess.recording_state = "stopped"
        sess.stopped = True
        if sess.job_file and not sess.source_ref:
            self._maybe_resolve_gcode(sess)
        if data.size >= 6 and sr > 0:
            # Prefer flute count from first resolved segment when available
            flutes = self.settings.default_flutes
            self._seed_segments(sess, t)
            if sess.segments:
                for seg in sess.segments:
                    if seg.get("flutes"):
                        flutes = int(seg["flutes"])
                        break
            analysis = analyze_session_arrays(
                t,
                data,
                sr,
                flute_count=flutes,
                max_rpm=self.settings.default_max_rpm,
                min_rpm=self.settings.default_min_rpm,
                spindle_freq_hz=self.settings.default_spindle_freq_hz,
                mode=sess.mode,
            )
            analysis["session_id"] = sess.session_id
            analysis["ts"] = time.time()
            if "jarvis" not in sess.analysis_by_source:
                sess.analysis_by_source["jarvis"] = analysis
            self._recompute_segment_analysis(sess)
            if self.settings.publish_jarvis_analysis and self._publish_fn is not None:
                topic = f"tap/{sess.device_id}/analysis"
                try:
                    self._publish_fn(topic, analysis, 1)
                except Exception as e:
                    logger.warning("Failed to publish jarvis analysis: {}", e)
            if self.settings.llm_forward_enabled:
                await self._forward_llm(sess, analysis)
        else:
            self._seed_segments(sess, t)
            self._recompute_segment_analysis(sess)
        self._write_ui_preview_from_arrays(sess, t, data, sr or sess.sample_rate_hz)
        self._persist(sess)

    def _npz_path(self, session_id: str) -> Path:
        return self.settings.session_store_dir / f"{session_id}.npz"

    def _ui_preview_path(self, session_id: str) -> Path:
        return self.settings.session_store_dir / f"{session_id}.ui.json"

    def _spectrum_from_arrays(
        self,
        data: np.ndarray,
        sr: float,
        *,
        max_bins: int = UI_PREVIEW_MAX_BINS,
    ) -> tuple[dict[str, Any], int]:
        """Build spectrum payload from accel arrays; returns (spectrum, n_samples_used)."""
        if data.size == 0 or data.ndim != 2 or data.shape[0] < 3:
            return (
                live_spectrum(
                    [],
                    [],
                    [],
                    float(sr or 0),
                    spindle_freq_hz=self.settings.default_spindle_freq_hz,
                ),
                0,
            )
        n_max = min(data.shape[1], max(8192, int(sr * 120) if sr > 0 else 200_000))
        ax = data[0, -n_max:]
        ay = data[1, -n_max:]
        az = data[2, -n_max:]
        spectrum = live_spectrum(
            ax, ay, az, sr, spindle_freq_hz=self.settings.default_spindle_freq_hz
        )
        max_bins = max(16, int(max_bins))
        freqs = spectrum.get("freqs_hz") or []
        mag = spectrum.get("mag") or []
        if len(freqs) > max_bins:
            step = max(1, len(freqs) // max_bins)
            spectrum = {
                **spectrum,
                "freqs_hz": freqs[::step][:max_bins],
                "mag": mag[::step][:max_bins],
            }
        return spectrum, int(ax.size)

    def _build_ui_preview(
        self,
        sess: SessionState,
        t: np.ndarray,
        data: np.ndarray,
        sr: float,
        *,
        max_points: int = UI_PREVIEW_MAX_POINTS,
        max_bins: int = UI_PREVIEW_MAX_BINS,
    ) -> dict[str, Any]:
        series = downsample_series(t, data, max_points)
        spectrum, n_used = self._spectrum_from_arrays(data, sr, max_bins=max_bins)
        return {
            "schema_version": UI_PREVIEW_SCHEMA_VERSION,
            "session_id": sess.session_id,
            "sample_rate_hz": float(sr or sess.sample_rate_hz or 0),
            "t_start": float(t[0]) if getattr(t, "size", 0) else sess.t_start,
            "t_end": float(t[-1]) if getattr(t, "size", 0) else sess.t_end,
            "n_samples_total": int(data.shape[1]) if data.ndim == 2 and data.size else 0,
            "n_samples_used": n_used,
            "max_points": int(max_points),
            "max_bins": int(max_bins),
            "series": series,
            "spectrum": spectrum,
        }

    def _write_ui_preview(self, session_id: str, preview: dict[str, Any]) -> None:
        path = self._ui_preview_path(session_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(preview), encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to write UI preview for {}: {}", session_id, e)

    def _write_ui_preview_from_arrays(
        self,
        sess: SessionState,
        t: np.ndarray,
        data: np.ndarray,
        sr: float,
    ) -> None:
        if data.size == 0 or getattr(t, "size", 0) == 0:
            return
        preview = self._build_ui_preview(sess, t, data, float(sr or 0))
        self._write_ui_preview(sess.session_id, preview)

    def _read_ui_preview(self, session_id: str) -> dict[str, Any] | None:
        path = self._ui_preview_path(session_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to read UI preview for {}: {}", session_id, e)
            return None
        if not isinstance(data, dict):
            return None
        if int(data.get("schema_version") or 0) != UI_PREVIEW_SCHEMA_VERSION:
            return None
        if not isinstance(data.get("series"), dict) or not isinstance(data.get("spectrum"), dict):
            return None
        return data

    def _refresh_ui_preview(self, sess: SessionState) -> dict[str, Any] | None:
        """Rebuild and persist UI chart preview from NPZ/RAM arrays."""
        loaded = self._load_arrays(sess.session_id)
        if loaded is None:
            t, data, sr = sess.reassembled()
            if data.size == 0:
                return None
        else:
            t, data, sr = loaded
        preview = self._build_ui_preview(sess, t, data, float(sr or sess.sample_rate_hz or 0))
        self._write_ui_preview(sess.session_id, preview)
        return preview

    def _persist(self, sess: SessionState) -> None:
        path = self.settings.session_store_dir / f"{sess.session_id}.json"
        payload = {
            "session_id": sess.session_id,
            "device_id": sess.device_id,
            "mode": sess.mode,
            "sample_rate_hz": sess.sample_rate_hz,
            "started_at": sess.started_at,
            "updated_at": sess.updated_at,
            "stopped": sess.stopped,
            "recording_state": sess.recording_state,
            "n_batches": len(sess.batches),
            "n_modbus_rows": len(sess.modbus_rows),
            "modbus_rows": sess.modbus_rows[-self.settings.modbus_rows_max :],
            "n_samples": sess.n_samples,
            "origin": sess.origin,
            "import_label": sess.import_label,
            "notes": sess.notes,
            "job_file": sess.job_file,
            "job_file_source": sess.job_file_source,
            "source_ref": sess.source_ref,
            "gcode_sha256": sess.gcode_sha256,
            "gcode_resolution_status": sess.gcode_resolution_status,
            "gcode_summary": sess.gcode_summary,
            "n_tool_events": len(sess.tool_events),
            "tool_events": sess.tool_events,
            "tools_snapshot": sess.tools_snapshot,
            "tools_snapshot_history": sess.tools_snapshot_history[-5:],
            "n_status_events": len(sess.status_events),
            "status_events": sess.status_events[-100:],
            "has_timeseries": bool(
                sess.timeseries_path and sess.timeseries_path.is_file()
            ),
            "analysis_by_source": sess.analysis_by_source,
            "segments": sess.segments,
            "segment_analysis": sess.segment_analysis,
            "rpm_samples": sess.rpm_samples,
            "n_rpm_samples": len(sess.rpm_samples),
            "load_samples": sess.load_samples,
            "n_load_samples": len(sess.load_samples),
            "transition_windows": sess.transition_windows,
            "rpm_alignment": sess.rpm_alignment,
            **{
                k: v
                for k, v in self._rpm_telemetry_summary(sess).items()
                if k
                not in (
                    "n_modbus_rows",
                    "n_rpm_samples",
                    "transition_windows",
                    "n_transition_windows",
                )
            },
            "n_transition_windows": len(sess.transition_windows),
            "run_meta": sess.run_meta,
            "sidecars": sess.sidecars,
            "t_start": sess.t_start,
            "t_end": sess.t_end,
            "n_segments": len(sess.segments),
            "last_sample_at": sess.last_sample_at,
            "last_batch_seq": sess.last_batch_seq,
            "active_tool_number": sess.active_tool_number,
            "active_tool_name": sess.active_tool_name,
            "active_tool_guid": sess.active_tool_guid,
            "active_segment_id": sess.active_segment_id,
            "publisher_state": sess.publisher_state,
            "warnings": sess.warnings[-20:],
            "motion_samples": sess.motion_samples,
            "n_motion_samples": len(sess.motion_samples),
            "latest_motion": sess.latest_motion,
        }
        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to persist session {}: {}", sess.session_id, e)

    def _hydrate_from_disk(self, session_id: str) -> SessionState | None:
        """Load a disk-only session into memory (npz path + metadata)."""
        if session_id in self.sessions:
            return self.sessions[session_id]
        path = self.settings.session_store_dir / f"{session_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        npz = self._npz_path(session_id)
        sess = SessionState(
            session_id=session_id,
            device_id=str(data.get("device_id") or "unknown"),
            mode=str(data.get("mode") or "tap"),
            sample_rate_hz=float(data.get("sample_rate_hz") or 800.0),
            started_at=float(data.get("started_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            stopped=bool(data.get("stopped", True)),
            recording_state=str(
                data.get("recording_state")
                or ("stopped" if data.get("stopped", True) else "idle")
            ),
            analysis_by_source=dict(data.get("analysis_by_source") or {}),
            status_events=list(data.get("status_events") or []),
            tool_events=list(data.get("tool_events") or []),
            tools_snapshot=list(data.get("tools_snapshot") or []),
            tools_snapshot_history=list(data.get("tools_snapshot_history") or []),
            job_file=data.get("job_file"),
            job_file_source=data.get("job_file_source"),
            source_ref=data.get("source_ref"),
            gcode_sha256=data.get("gcode_sha256"),
            gcode_resolution_status=data.get("gcode_resolution_status"),
            gcode_summary=data.get("gcode_summary"),
            timeseries_path=npz if npz.is_file() else None,
            n_samples=int(data.get("n_samples") or 0),
            import_label=data.get("import_label"),
            notes=data.get("notes"),
            origin=str(data.get("origin") or "mqtt"),
            segments=list(data.get("segments") or []),
            segment_analysis=list(data.get("segment_analysis") or []),
            rpm_samples=list(data.get("rpm_samples") or []),
            load_samples=list(data.get("load_samples") or []),
            modbus_rows=list(data.get("modbus_rows") or []),
            transition_windows=list(data.get("transition_windows") or []),
            rpm_alignment=str(data.get("rpm_alignment") or "unknown"),
            run_meta=dict(data.get("run_meta") or {}),
            sidecars=dict(data.get("sidecars") or {}),
            t_start=data.get("t_start"),
            t_end=data.get("t_end"),
            last_sample_at=data.get("last_sample_at"),
            last_batch_seq=data.get("last_batch_seq"),
            active_tool_number=data.get("active_tool_number"),
            active_tool_name=data.get("active_tool_name"),
            active_tool_guid=data.get("active_tool_guid"),
            active_segment_id=data.get("active_segment_id"),
            publisher_state=data.get("publisher_state"),
            warnings=list(data.get("warnings") or []),
            motion_samples=list(data.get("motion_samples") or []),
        )
        if sess.motion_samples:
            sess.latest_motion = sess.motion_samples[-1]
        sess.seen_tool_event_ids = {
            str(ev["event_id"])
            for ev in sess.tool_events
            if isinstance(ev, dict) and ev.get("event_id")
        }
        sess.seen_rpm_keys = {
            (
                round(float(s.get("t_s", 0.0)), 6),
                str(s.get("source") or ""),
                s.get("raw_value"),
            )
            for s in sess.rpm_samples
        }
        if not sess.transition_windows:
            self._rebuild_transition_windows(sess)
        self.sessions[session_id] = sess
        return sess

    async def import_upload(
        self,
        raw: bytes,
        *,
        filename: str = "",
        mode: str = "live_spindle",
        device_id: str = "import",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Parse CSV/ZIP upload (incl. sidecars), persist npz + JSON, run analysis."""
        bundle = load_run_bundle(raw, filename=filename)
        t = bundle["t"]
        data = bundle["data"]
        sr = bundle["sample_rate_hz"]
        label = bundle["source_label"]
        sid = session_id or f"imp-{uuid.uuid4().hex[:12]}"
        # Prefer session_id from run-meta when present
        meta = bundle.get("run_meta") or {}
        if meta.get("session_id") and not session_id:
            sid = f"imp-{str(meta['session_id'])[:40]}"
        npz_path = self._npz_path(sid)
        async with self._lock:
            save_timeseries_npz(npz_path, t, data, sr)
            job_file = meta.get("job_file")
            sess = SessionState(
                session_id=sid,
                device_id=device_id,
                mode=mode or str(meta.get("mode") or "live_spindle"),
                sample_rate_hz=sr,
                stopped=True,
                timeseries_path=npz_path,
                n_samples=int(t.size),
                import_label=label or filename or None,
                origin="import",
                tool_events=list(bundle.get("tool_events") or []),
                tools_snapshot=list(bundle.get("tools_snapshot") or []),
                rpm_samples=list(bundle.get("rpm_samples") or []),
                load_samples=list(bundle.get("load_samples") or []),
                run_meta=meta,
                sidecars=dict(bundle.get("sidecars") or {}),
                job_file=str(job_file) if job_file else None,
                rpm_alignment=(
                    "ok"
                    if any(
                        is_observed_source(str(s.get("source")))
                        and str(s.get("source")) != "manual"
                        for s in (bundle.get("rpm_samples") or [])
                    )
                    else "none"
                ),
            )
            sess.seen_rpm_keys = {
                (
                    round(float(s.get("t_s", 0.0)), 6),
                    str(s.get("source") or ""),
                    s.get("raw_value"),
                )
                for s in sess.rpm_samples
            }
            if t.size:
                sess.t_start = float(t[0])
                sess.t_end = float(t[-1])
            self._rebuild_transition_windows(sess)
            self.sessions[sid] = sess
            await self._finalize_session(sess)
            return self.get_session(sid) or {"session_id": sid}

    async def set_segments(
        self,
        session_id: str,
        segments: list[dict[str, Any]],
        *,
        reanalyze: bool = True,
    ) -> dict[str, Any]:
        """Replace session segments after validation; optionally recompute analytics."""
        async with self._lock:
            sess = self.sessions.get(session_id) or self._hydrate_from_disk(session_id)
            if sess is None:
                raise KeyError(session_id)
            t, data, sr = sess.reassembled()
            t_min, t_max = self._time_bounds(t) if t.size else (None, None)
            errors = validate_segments(segments, t_min=t_min, t_max=t_max)
            if errors:
                raise ValueError("; ".join(errors))
            normalized = [normalize_segment(s) for s in segments]
            normalized = enrich_segments_from_rrf_snapshot(normalized, sess.tools_snapshot)
            sess.segments = self._apply_tool_resolution(normalized)
            sess.updated_at = time.time()
            if reanalyze and data.size >= 6 and sr > 0:
                # Refresh whole-session analysis with first segment flute count
                flutes = self.settings.default_flutes
                for seg in sess.segments:
                    if seg.get("flutes"):
                        flutes = int(seg["flutes"])
                        break
                analysis = analyze_session_arrays(
                    t,
                    data,
                    sr,
                    flute_count=flutes,
                    max_rpm=self.settings.default_max_rpm,
                    min_rpm=self.settings.default_min_rpm,
                    spindle_freq_hz=self.settings.default_spindle_freq_hz,
                    mode=sess.mode,
                )
                analysis["session_id"] = sess.session_id
                analysis["ts"] = time.time()
                sess.analysis_by_source["jarvis"] = analysis
                self._recompute_segment_analysis(sess)
            elif reanalyze:
                self._recompute_segment_analysis(sess)
            if reanalyze and data.size > 0:
                self._write_ui_preview_from_arrays(sess, t, data, sr or sess.sample_rate_hz)
            self._persist(sess)
            return self.get_session(session_id) or {"session_id": session_id}

    def get_segment_analysis(self, session_id: str) -> dict[str, Any] | None:
        sess = self.sessions.get(session_id) or self._hydrate_from_disk(session_id)
        if sess is None:
            return None
        if not sess.segment_analysis and sess.segments:
            self._recompute_segment_analysis(sess)
            self._persist(sess)
        return {
            "session_id": session_id,
            "segments": sess.segments,
            "segment_analysis": sess.segment_analysis,
            "rpm_samples": sess.rpm_samples,
            "t_start": sess.t_start,
            "t_end": sess.t_end,
            "analysis_by_source": sess.analysis_by_source,
            "disclaimer": (
                "Predicted tooth-pass resonance risk from response spectrum; "
                "observed good/bad bands require time-aligned RPM telemetry. "
                "Not a full stability-lobe or safety determination."
            ),
        }

    async def _forward_llm(self, sess: SessionState, analysis: dict[str, Any]) -> None:
        base = self.settings.jarvis_llm_base_url
        if not base:
            return
        url = base.rstrip("/") + "/events"
        body = {
            "event_type": "data_analysis",
            "source": "tap-testing",
            "content": {
                "data": {
                    "session_id": sess.session_id,
                    "device_id": sess.device_id,
                    "mode": sess.mode,
                    "analysis": {
                        k: analysis.get(k)
                        for k in (
                            "source",
                            "engine",
                            "fn_hz",
                            "fn_hz_uncertainty",
                            "avoid_rpm",
                            "suggested_rpm_min",
                            "suggested_rpm_max",
                            "n_teeth",
                            "sample_rate_hz",
                        )
                    },
                    "n_segments": len(sess.segments),
                }
            },
            "metadata": {"model_tier": "fast"},
        }
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": f"tap-{sess.session_id}-{analysis.get('source', 'jarvis')}",
            "X-Request-ID": str(uuid.uuid4()),
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, json=body, headers=headers)
                if r.status_code >= 400:
                    logger.warning("LLM forward HTTP {}: {}", r.status_code, r.text[:200])
                else:
                    logger.info("Forwarded analysis for {} to LLM", sess.session_id)
        except Exception as e:
            logger.warning("LLM forward failed: {}", e)

    async def sweep_idle(self) -> None:
        """Finalize sessions idle past timeout (or max wall-clock) without an explicit stop."""
        now = time.time()
        timeout = self.settings.session_idle_timeout_s
        max_s = float(self.settings.max_session_s or 0.0)
        async with self._lock:
            for sess in list(self.sessions.values()):
                if sess.stopped:
                    continue
                # Mark stalled when no samples recently but still open
                if (
                    sess.recording_state == "recording"
                    and sess.last_sample_at
                    and now - sess.last_sample_at >= min(10.0, timeout)
                ):
                    sess.recording_state = "stalled"
                idle_due = now - sess.updated_at >= timeout
                max_due = max_s > 0 and (now - sess.started_at) >= max_s
                if not idle_due and not max_due:
                    continue
                if not sess.batches:
                    if "idle_timeout_empty" not in sess.warnings:
                        sess.warnings.append("idle_timeout_empty")
                if max_due and "max_session_timeout" not in sess.warnings:
                    sess.warnings.append("max_session_timeout")
                sess.stopped = True
                sess.recording_state = "stopped"
                self._close_open_segment(
                    sess, t1=self._latest_t_s(sess), analyze=True
                )
                reason = "max-session" if max_due else "idle"
                logger.info("{} finalize {}", reason, sess.session_id)
                await self._finalize_session(sess)
        # Retention prune for persisted MQTT event JSONL (throttled inside store)
        try:
            self.mqtt_events.maybe_prune()
        except Exception:
            pass

    async def force_stop_session(self, session_id: str) -> dict[str, Any]:
        """Force-finalize a live session (ops / SPA when MQTT stop never arrives)."""
        async with self._lock:
            sess = self.sessions.get(session_id)
            if sess is None:
                # Disk-only already stopped sessions
                disk = self.get_session(session_id)
                if disk is None:
                    raise KeyError(session_id)
                if disk.get("stopped") or disk.get("recording_state") == "stopped":
                    return {"session_id": session_id, "already_stopped": True, **disk}
                raise KeyError(session_id)
            if sess.stopped:
                data = self.get_session(session_id) or {}
                return {"session_id": session_id, "already_stopped": True, **data}
            if "force_stop" not in sess.warnings:
                sess.warnings.append("force_stop")
            if not sess.batches and "idle_timeout_empty" not in sess.warnings:
                # Reuse empty marker when stopping with no samples
                sess.warnings.append("idle_timeout_empty")
            sess.stopped = True
            sess.recording_state = "stopped"
            self._close_open_segment(
                sess, t1=self._latest_t_s(sess), analyze=True
            )
            logger.info("Force-stop finalize {}", sess.session_id)
            await self._finalize_session(sess)
            data = self.get_session(session_id) or {"session_id": session_id}
            return {"session_id": session_id, "already_stopped": False, **data}

    async def delete_session(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        """Remove session from memory and delete JSON/NPZ/UI preview artifacts."""
        async with self._lock:
            sess = self.sessions.get(session_id)
            json_path = self.settings.session_store_dir / f"{session_id}.json"
            npz_path = self._npz_path(session_id)
            on_disk = json_path.is_file() or npz_path.is_file()
            if sess is None and not on_disk:
                raise KeyError(session_id)
            if sess is not None and not sess.stopped:
                if not force:
                    raise PermissionError("session_recording")
                if "force_stop" not in sess.warnings:
                    sess.warnings.append("force_stop")
                sess.stopped = True
                sess.recording_state = "stopped"
                self._close_open_segment(
                    sess, t1=self._latest_t_s(sess), analyze=False
                )
                await self._finalize_session(sess)
            self.sessions.pop(session_id, None)
            removed: list[str] = []
            ui_path = self._ui_preview_path(session_id)
            for path in (json_path, npz_path, ui_path):
                if path.is_file():
                    try:
                        path.unlink()
                        removed.append(path.name)
                    except OSError as e:
                        logger.warning("Failed to delete {}: {}", path, e)
            return {
                "session_id": session_id,
                "deleted": True,
                "removed": removed,
            }

    async def patch_session(
        self, session_id: str, *, notes: Any = ..., import_label: Any = ...
    ) -> dict[str, Any]:
        """Update allowlisted session metadata and persist."""
        async with self._lock:
            sess = self.sessions.get(session_id)
            if sess is None:
                sess = self._hydrate_from_disk(session_id)
            if sess is None:
                raise KeyError(session_id)
            if notes is not ...:
                if notes is None:
                    sess.notes = None
                else:
                    sess.notes = str(notes)
            if import_label is not ...:
                if import_label is None:
                    sess.import_label = None
                else:
                    sess.import_label = str(import_label)
            sess.updated_at = time.time()
            self._persist(sess)
            data = self.get_session(session_id)
            if data is None:
                raise KeyError(session_id)
            return data

    def _session_summary(self, s: SessionState) -> dict[str, Any]:
        active = next((x for x in s.segments if x.get("state") == "open"), None)
        return {
            "session_id": s.session_id,
            "device_id": s.device_id,
            "mode": s.mode,
            "stopped": s.stopped,
            "recording_state": s.recording_state,
            "n_batches": len(s.batches),
            "n_samples": s.n_samples,
            "origin": s.origin,
            "import_label": s.import_label,
            "notes": s.notes,
            "job_file": s.job_file,
            "source_ref": s.source_ref,
            "gcode_resolution_status": s.gcode_resolution_status,
            "n_tool_events": len(s.tool_events),
            "n_segments": len(s.segments),
            "active_tool_number": s.active_tool_number,
            "active_tool_name": s.active_tool_name,
            "active_tool_guid": s.active_tool_guid,
            "active_segment_id": s.active_segment_id,
            "active_offsets": (active or {}).get("offsets"),
            "last_sample_at": s.last_sample_at,
            "analysis_sources": list(s.analysis_by_source.keys()),
            "updated_at": s.updated_at,
            "sample_rate_hz": s.sample_rate_hz,
            "warnings": s.warnings[-5:],
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        for s in self.sessions.values():
            by_id[s.session_id] = self._session_summary(s)
        store = self.settings.session_store_dir
        if store.is_dir():
            for path in store.glob("*.json"):
                # Skip chart preview sidecars ({session_id}.ui.json)
                if path.name.endswith(".ui.json"):
                    continue
                sid = path.stem
                if sid in by_id:
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                by_id[sid] = {
                    "session_id": data.get("session_id", sid),
                    "device_id": data.get("device_id"),
                    "mode": data.get("mode"),
                    "stopped": data.get("stopped", True),
                    "recording_state": data.get("recording_state")
                    or ("stopped" if data.get("stopped", True) else "idle"),
                    "n_batches": data.get("n_batches", 0),
                    "n_samples": data.get("n_samples", 0),
                    "origin": data.get("origin", "mqtt"),
                    "import_label": data.get("import_label"),
                    "job_file": data.get("job_file"),
                    "n_tool_events": data.get(
                        "n_tool_events", len(data.get("tool_events") or [])
                    ),
                    "n_segments": data.get("n_segments", len(data.get("segments") or [])),
                    "active_tool_number": data.get("active_tool_number"),
                    "active_tool_name": data.get("active_tool_name"),
                    "active_tool_guid": data.get("active_tool_guid"),
                    "last_sample_at": data.get("last_sample_at"),
                    "analysis_sources": list((data.get("analysis_by_source") or {}).keys()),
                    "updated_at": data.get("updated_at", 0),
                    "sample_rate_hz": data.get("sample_rate_hz"),
                    "warnings": (data.get("warnings") or [])[-5:],
                }
        return sorted(by_id.values(), key=lambda x: x.get("updated_at") or 0, reverse=True)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        s = self.sessions.get(session_id)
        if s is None:
            path = self.settings.session_store_dir / f"{session_id}.json"
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
            return None
        return {
            "session_id": s.session_id,
            "device_id": s.device_id,
            "mode": s.mode,
            "sample_rate_hz": s.sample_rate_hz,
            "stopped": s.stopped,
            "recording_state": s.recording_state,
            "n_batches": len(s.batches),
            "n_modbus_rows": len(s.modbus_rows),
            "n_samples": s.n_samples,
            "origin": s.origin,
            "import_label": s.import_label,
            "notes": s.notes,
            "job_file": s.job_file,
            "job_file_source": s.job_file_source,
            "source_ref": s.source_ref,
            "gcode_sha256": s.gcode_sha256,
            "gcode_resolution_status": s.gcode_resolution_status,
            "gcode_summary": s.gcode_summary,
            "n_tool_events": len(s.tool_events),
            "tool_events": s.tool_events,
            "tools_snapshot": s.tools_snapshot,
            "tools_snapshot_history": s.tools_snapshot_history[-5:],
            "n_status_events": len(s.status_events),
            "status_events": s.status_events[-100:],
            "has_timeseries": bool(
                s.timeseries_path and s.timeseries_path.is_file()
            ),
            "analysis_by_source": s.analysis_by_source,
            "started_at": s.started_at,
            "updated_at": s.updated_at,
            "last_sample_at": s.last_sample_at,
            "last_batch_seq": s.last_batch_seq,
            "active_tool_number": s.active_tool_number,
            "active_tool_name": s.active_tool_name,
            "active_tool_guid": s.active_tool_guid,
            "active_segment_id": s.active_segment_id,
            "publisher_state": s.publisher_state,
            "warnings": s.warnings[-20:],
            "segments": s.segments,
            "segment_analysis": s.segment_analysis,
            "rpm_samples": s.rpm_samples,
            "n_rpm_samples": len(s.rpm_samples),
            "load_samples": s.load_samples,
            "n_load_samples": len(s.load_samples),
            "transition_windows": s.transition_windows,
            "rpm_alignment": s.rpm_alignment,
            **self._rpm_telemetry_summary(s),
            "run_meta": s.run_meta,
            "sidecars": s.sidecars,
            "t_start": s.t_start,
            "t_end": s.t_end,
            "n_segments": len(s.segments),
            "motion_samples": s.motion_samples[-max(50, int(self.settings.rpm_samples_ws_max)) :],
            "latest_motion": s.latest_motion,
            "n_motion_samples": len(s.motion_samples),
        }

    def _load_arrays(self, session_id: str) -> tuple[np.ndarray, np.ndarray, float] | None:
        s = self.sessions.get(session_id)
        if s is not None:
            t, data, sr = s.reassembled()
            if data.size > 0:
                return t, data, sr
        npz = self._npz_path(session_id)
        if npz.is_file():
            return load_timeseries_npz(npz)
        return None

    def series_view(
        self,
        session_id: str,
        max_points: int = 2000,
        *,
        regenerate: bool = False,
    ) -> dict[str, Any] | None:
        meta = self.get_session(session_id)
        max_points = max(8, int(max_points))
        if not regenerate:
            preview = self._read_ui_preview(session_id)
            if preview is not None:
                series = preview.get("series") or {}
                cached_n = int(series.get("n_returned") or 0)
                cached_max = int(preview.get("max_points") or cached_n or 0)
                if cached_max >= max_points or cached_n > 0:
                    if cached_n > max_points:
                        series = self._stride_series_dict(series, max_points)
                    return {
                        "session_id": session_id,
                        "sample_rate_hz": preview.get("sample_rate_hz")
                        or (meta or {}).get("sample_rate_hz"),
                        "series": series,
                        "segments": (meta or {}).get("segments") or [],
                        "t_start": preview.get("t_start"),
                        "t_end": preview.get("t_end"),
                        "from_cache": True,
                    }

        loaded = self._load_arrays(session_id)
        if loaded is None:
            if meta is None:
                return None
            return {
                "session_id": session_id,
                "sample_rate_hz": meta.get("sample_rate_hz"),
                "series": downsample_series(np.asarray([]), np.zeros((3, 0)), max_points),
                "segments": meta.get("segments") or [],
                "from_cache": False,
            }
        t, data, sr = loaded
        series = downsample_series(t, data, max_points)
        # Persist a standard-size preview for future views
        sess = self.sessions.get(session_id)
        if sess is None:
            sess = self._hydrate_from_disk(session_id)
        if sess is not None:
            self._write_ui_preview_from_arrays(sess, t, data, sr)
        return {
            "session_id": session_id,
            "sample_rate_hz": sr,
            "series": series,
            "segments": (meta or {}).get("segments") or [],
            "t_start": float(t[0]) if t.size else None,
            "t_end": float(t[-1]) if t.size else None,
            "from_cache": False,
        }

    @staticmethod
    def _stride_series_dict(series: dict[str, Any], max_points: int) -> dict[str, Any]:
        t_s = list(series.get("t_s") or [])
        n = len(t_s)
        if n == 0 or n <= max_points:
            return series
        step = max(1, (n + max_points - 1) // max_points)
        ax = list(series.get("ax_g") or [])
        ay = list(series.get("ay_g") or [])
        az = list(series.get("az_g") or [])
        idx = list(range(0, n, step))
        return {
            "t_s": [t_s[i] for i in idx],
            "ax_g": [ax[i] for i in idx] if len(ax) == n else [],
            "ay_g": [ay[i] for i in idx] if len(ay) == n else [],
            "az_g": [az[i] for i in idx] if len(az) == n else [],
            "n_total": int(series.get("n_total") or n),
            "n_returned": len(idx),
            "stride": int(series.get("stride") or 1) * step,
        }

    def spectrum_view(
        self,
        session_id: str,
        max_bins: int = 256,
        *,
        regenerate: bool = False,
    ) -> dict[str, Any] | None:
        meta = self.get_session(session_id)
        max_bins = max(16, int(max_bins))
        if not regenerate:
            preview = self._read_ui_preview(session_id)
            if preview is not None and isinstance(preview.get("spectrum"), dict):
                spectrum = dict(preview["spectrum"])
                freqs = list(spectrum.get("freqs_hz") or [])
                mag = list(spectrum.get("mag") or [])
                if len(freqs) > max_bins:
                    step = max(1, len(freqs) // max_bins)
                    spectrum = {
                        **spectrum,
                        "freqs_hz": freqs[::step][:max_bins],
                        "mag": mag[::step][:max_bins],
                    }
                return {
                    "session_id": session_id,
                    "sample_rate_hz": preview.get("sample_rate_hz")
                    or (meta or {}).get("sample_rate_hz"),
                    "n_samples_used": preview.get("n_samples_used"),
                    "n_samples_total": preview.get("n_samples_total")
                    or (meta or {}).get("n_samples"),
                    "spectrum": spectrum,
                    "analysis_by_source": (meta or {}).get("analysis_by_source")
                    or (
                        self.sessions[session_id].analysis_by_source
                        if session_id in self.sessions
                        else {}
                    ),
                    "segments": (meta or {}).get("segments") or [],
                    "segment_analysis": (meta or {}).get("segment_analysis") or [],
                    "from_cache": True,
                }

        loaded = self._load_arrays(session_id)
        if loaded is None:
            if meta is None:
                return None
            return {
                "session_id": session_id,
                "sample_rate_hz": meta.get("sample_rate_hz"),
                "spectrum": live_spectrum(
                    [],
                    [],
                    [],
                    float(meta.get("sample_rate_hz") or 0),
                    spindle_freq_hz=self.settings.default_spindle_freq_hz,
                ),
                "analysis_by_source": meta.get("analysis_by_source") or {},
                "from_cache": False,
            }
        t, data, sr = loaded
        spectrum, n_used = self._spectrum_from_arrays(data, sr, max_bins=max_bins)
        sess = self.sessions.get(session_id)
        if sess is None:
            sess = self._hydrate_from_disk(session_id)
        if sess is not None:
            self._write_ui_preview_from_arrays(sess, t, data, sr)
        return {
            "session_id": session_id,
            "sample_rate_hz": sr,
            "n_samples_used": n_used,
            "n_samples_total": int(data.shape[1]),
            "spectrum": spectrum,
            "analysis_by_source": (meta or {}).get("analysis_by_source")
            or (self.sessions[session_id].analysis_by_source if session_id in self.sessions else {}),
            "segments": (meta or {}).get("segments") or [],
            "segment_analysis": (meta or {}).get("segment_analysis") or [],
            "from_cache": False,
        }

    def _live_meta(self, s: SessionState) -> dict[str, Any]:
        active = next((x for x in s.segments if x.get("state") == "open"), None)
        age = None
        if s.last_sample_at:
            age = max(0.0, time.time() - float(s.last_sample_at))
        ws_max = max(50, int(self.settings.rpm_samples_ws_max))
        rpm_summary = self._rpm_telemetry_summary(s)
        return {
            "recording_state": s.recording_state,
            "stopped": s.stopped,
            "active_tool_number": s.active_tool_number,
            "active_tool_name": s.active_tool_name,
            "active_tool_guid": s.active_tool_guid,
            "active_segment_id": s.active_segment_id,
            "active_segment": active,
            "active_offsets": (active or {}).get("offsets"),
            "n_batches": len(s.batches),
            "n_samples_total": s.n_samples or (
                sum(
                    min(len(b.get("ax") or []), len(b.get("ay") or []), len(b.get("az") or []))
                    for b in s.batches.values()
                )
            ),
            "n_tool_events": len(s.tool_events),
            "n_segments": len(s.segments),
            "last_sample_at": s.last_sample_at,
            "sample_age_s": age,
            "publisher_state": s.publisher_state,
            "job_file": s.job_file,
            "source_ref": s.source_ref,
            "gcode_resolution_status": s.gcode_resolution_status,
            "warnings": s.warnings[-10:],
            "t_start": s.t_start,
            "t_end": s.t_end,
            # Live frames keep a trailing window; stopped sessions send full history
            # so the SPA does not clobber early-tool measured/commanded series.
            "rpm_samples": (
                s.rpm_samples if s.stopped else s.rpm_samples[-ws_max:]
            ),
            "load_samples": (
                s.load_samples if s.stopped else s.load_samples[-ws_max:]
            ),
            "motion_samples": (
                s.motion_samples if s.stopped else s.motion_samples[-ws_max:]
            ),
            "latest_motion": s.latest_motion,
            "n_motion_samples": len(s.motion_samples),
            **rpm_summary,
        }

    def live_view(self, session_id: str) -> dict[str, Any] | None:
        s = self.sessions.get(session_id)
        if s is None:
            # Imported / disk-only sessions: full spectrum snapshot + disk meta
            snap = self.spectrum_view(session_id)
            if snap is None:
                return None
            meta = self.get_session(session_id) or {}
            rpm_samples = list(meta.get("rpm_samples") or [])
            snap.update(
                {
                    "recording_state": meta.get("recording_state") or "stopped",
                    "stopped": meta.get("stopped", True),
                    "active_tool_number": meta.get("active_tool_number"),
                    "active_tool_name": meta.get("active_tool_name"),
                    "active_tool_guid": meta.get("active_tool_guid"),
                    "segments": meta.get("segments") or snap.get("segments") or [],
                    "segment_analysis": meta.get("segment_analysis")
                    or snap.get("segment_analysis")
                    or [],
                    # Disk/stopped sessions: full history (do not truncate for WS).
                    "rpm_samples": rpm_samples,
                    "n_rpm_samples": meta.get("n_rpm_samples", len(rpm_samples)),
                    "load_samples": list(meta.get("load_samples") or []),
                    "n_load_samples": meta.get("n_load_samples", 0),
                    "n_modbus_rows": meta.get("n_modbus_rows", 0),
                    "transition_windows": meta.get("transition_windows") or [],
                    "latest_measured_rpm": meta.get("latest_measured_rpm"),
                    "latest_commanded_rpm": meta.get("latest_commanded_rpm"),
                    "latest_measured_minus_commanded": meta.get(
                        "latest_measured_minus_commanded"
                    ),
                    "latest_load_percent": meta.get("latest_load_percent"),
                    "latest_watts": meta.get("latest_watts"),
                    "rpm_alignment": meta.get("rpm_alignment") or "unknown",
                    "has_aligned_measured_rpm": meta.get("has_aligned_measured_rpm"),
                    "final": True,
                }
            )
            return snap
        t, data, sr = s.reassembled()
        meta = self._live_meta(s)
        if data.size == 0:
            return {
                "session_id": session_id,
                "sample_rate_hz": sr,
                "spectrum": live_spectrum(
                    [], [], [], sr, spindle_freq_hz=self.settings.default_spindle_freq_hz
                ),
                "analysis_by_source": s.analysis_by_source,
                "segments": s.segments,
                "segment_analysis": s.segment_analysis,
                "final": s.stopped,
                **meta,
            }
        # last live_window_s of samples
        n_keep = max(8, int(self.settings.live_window_s * sr)) if sr > 0 else data.shape[1]
        ax = data[0, -n_keep:].tolist()
        ay = data[1, -n_keep:].tolist()
        az = data[2, -n_keep:].tolist()
        # Lightweight series downsample for live plot
        series = None
        try:
            from models.import_csv import downsample_series

            series = downsample_series(t, data, max_points=800)
        except Exception:
            series = None
        return {
            "session_id": session_id,
            "sample_rate_hz": sr,
            "n_samples_total": int(data.shape[1]),
            "spectrum": live_spectrum(
                ax, ay, az, sr, spindle_freq_hz=self.settings.default_spindle_freq_hz
            ),
            "series": series,
            "analysis_by_source": s.analysis_by_source,
            "segments": s.segments,
            "segment_analysis": s.segment_analysis,
            "final": s.stopped,
            **meta,
        }

    def motion_view(self, session_id: str, *, max_points: int = 2000) -> dict[str, Any] | None:
        s = self.sessions.get(session_id)
        if s is None:
            s = self._hydrate_from_disk(session_id)
        if s is None:
            meta = self.get_session(session_id)
            if meta is None:
                return None
            samples = list(meta.get("motion_samples") or [])
            return motion_view_payload(session_id, samples, max_points=max_points)
        return motion_view_payload(session_id, s.motion_samples, max_points=max_points)

    async def replay_session_from_log(
        self,
        session_id: str,
        *,
        include_batches: bool = False,
        restore_tools: bool = True,
        restore_motion: bool = True,
        reanalyze: bool = False,
    ) -> dict[str, Any]:
        log_dir = self.settings.telemetry_log_dir
        if log_dir is None or not log_dir.is_dir():
            return {
                "session_id": session_id,
                "error": "telemetry_log_unavailable",
                "detail": "TAP_COLLECTOR_TELEMETRY_LOG_DIR not configured or missing",
            }
        sess = self.sessions.get(session_id) or self._hydrate_from_disk(session_id)
        if sess is None:
            meta_path = self.settings.session_store_dir / f"{session_id}.json"
            if not meta_path.is_file() and not (log_dir / session_id).is_dir():
                raise KeyError(session_id)
            device_id = "replay"
            if (log_dir / session_id).is_dir():
                for row in iter_session_log_events(log_dir, session_id):
                    device_id = str(row.get("device_id") or device_id)
                    break
            sess = SessionState(session_id=session_id, device_id=device_id, origin="replay")
            self.sessions[session_id] = sess

        async with self._lock:
            before_tools = len(sess.tool_events)
            tools_added = 0
            if restore_tools:
                tools_added = self._merge_tool_events_from_durable(sess)

            before_motion = len(sess.motion_samples)
            before_rpm = len(sess.rpm_samples)
            added_motion = 0
            added_rpm = 0
            if restore_motion:
                # Force replace from durable log for explicit replay.
                sess.recover_from_jsonl = True
                t_start = sess.t_start
                if t_start is None and sess.timeseries_path and sess.timeseries_path.is_file():
                    try:
                        t, _d, _sr = load_timeseries_npz(sess.timeseries_path)
                        if t.size:
                            t_start = float(t[0])
                    except Exception:
                        t_start = None
                # Clear so _maybe_restore always reloads when durable rows exist.
                if self._motion_jsonl_path(session_id) is not None:
                    sess.motion_samples = []
                    sess.latest_motion = None
                added_motion = self._maybe_restore_motion_from_log(sess, t_start=t_start)
                if added_motion == 0 and before_motion:
                    # nothing on disk; keep prior samples
                    pass
                added_rpm = self._maybe_restore_rpm_from_durable(sess, t_start=t_start)
                if added_rpm == 0 and before_rpm and not sess.rpm_samples:
                    pass

            added_batches = 0
            if include_batches:

                def _on_batch(payload: dict[str, Any]) -> None:
                    seq = payload.get("seq")
                    if seq is None:
                        return
                    try:
                        seq_i = int(seq)
                    except (TypeError, ValueError):
                        return
                    sess.batches[seq_i] = payload

                added_batches = replay_batches_from_log(log_dir, session_id, _on_batch)

            if reanalyze or (restore_tools and tools_added):
                t, data, sr = self._resolve_finalize_arrays(sess)
                if data.size == 0 and sess.timeseries_path and sess.timeseries_path.is_file():
                    try:
                        t, data, sr = load_timeseries_npz(sess.timeseries_path)
                    except Exception:
                        pass
                if getattr(t, "size", 0):
                    sess.t_start, sess.t_end = self._time_bounds(t)
                    self._seed_segments(sess, t)
                    if data.size >= 6 and sr > 0:
                        flutes = self.settings.default_flutes
                        for seg in sess.segments:
                            if seg.get("flutes"):
                                flutes = int(seg["flutes"])
                                break
                        analysis = analyze_session_arrays(
                            t,
                            data,
                            sr,
                            flute_count=flutes,
                            max_rpm=self.settings.default_max_rpm,
                            min_rpm=self.settings.default_min_rpm,
                            spindle_freq_hz=self.settings.default_spindle_freq_hz,
                            mode=sess.mode,
                        )
                        analysis["session_id"] = sess.session_id
                        analysis["ts"] = time.time()
                        sess.analysis_by_source["jarvis"] = analysis
                        self._recompute_segment_analysis(sess)
                        self._write_ui_preview_from_arrays(sess, t, data, sr)

            sess.updated_at = time.time()
            self._persist(sess)

        return {
            "session_id": session_id,
            "tool_events_before": before_tools,
            "tool_events_added": tools_added,
            "tool_events_total": len(sess.tool_events),
            "n_segments": len(sess.segments),
            "motion_samples_before": before_motion,
            "motion_samples_added": added_motion,
            "motion_samples_total": len(sess.motion_samples),
            "rpm_samples_before": before_rpm,
            "rpm_samples_added": added_rpm,
            "rpm_samples_total": len(sess.rpm_samples),
            "batches_replayed": added_batches if include_batches else 0,
            "n_batches": len(sess.batches),
            "reanalyzed": bool(reanalyze or (restore_tools and tools_added)),
        }
