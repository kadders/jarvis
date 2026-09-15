"""Collector recovery from durable accel.jsonl after mid-job restart."""

from __future__ import annotations

import asyncio
import json
import os
import time

import numpy as np

from models.handler import TapCollectorHandler
from models.replay import peek_accel_jsonl_meta, reassemble_accel_jsonl
from utils.config import Settings


def _settings(tmp_path, **kwargs):
    base = dict(
        session_store_dir=tmp_path / "sessions",
        telemetry_log_dir=tmp_path / "telemetry",
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
        session_idle_timeout_s=60.0,
    )
    base.update(kwargs)
    return Settings(**base)


def _write_accel_jsonl(path, session_id: str, batches: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for b in batches:
            row = {
                "session_id": session_id,
                "device_id": "milo",
                "_device_id": "milo",
                "_ingest_ts": 1_700_000_000.0 + float(b["seq"]),
                "_leaf": "accel/batch",
                "payload": b,
                **b,
            }
            fh.write(json.dumps(row) + "\n")


def _batch(session_id: str, seq: int, t0: float, n: int = 4, dt: float = 0.01):
    ax = [0.1 * seq] * n
    ay = [0.2] * n
    az = [-1.0] * n
    return {
        "session_id": session_id,
        "seq": seq,
        "t0_s": t0,
        "dt_s": dt,
        "ax": ax,
        "ay": ay,
        "az": az,
    }


def test_reassemble_accel_jsonl(tmp_path):
    sid = "s_jsonl"
    path = tmp_path / "telemetry" / sid / "accel.jsonl"
    batches = [
        _batch(sid, 0, 10.0),
        _batch(sid, 1, 10.04),
        _batch(sid, 2, 10.08),
    ]
    _write_accel_jsonl(path, sid, batches)
    meta = peek_accel_jsonl_meta(path)
    assert meta is not None
    assert meta["t0_s"] == 10.0
    t, data, sr, n = reassemble_accel_jsonl(path, sample_rate_hz=100.0, session_id=sid)
    assert n == 3
    assert t.size == 12
    assert abs(float(t[0]) - 10.0) < 1e-9
    assert data.shape == (3, 12)
    assert sr == 100.0


def test_finalize_prefers_jsonl_after_restart(tmp_path):
    """Simulate collector restart: only late batches in RAM; full history on disk."""
    sid = "20260906_fake"
    settings = _settings(tmp_path)
    log_path = settings.telemetry_log_dir / sid / "accel.jsonl"
    all_batches = [_batch(sid, i, 100.0 + i * 0.04) for i in range(10)]
    _write_accel_jsonl(log_path, sid, all_batches)

    handler = TapCollectorHandler(settings)

    async def run():
        # Post-restart: only last two batches arrive in memory
        for b in all_batches[-2:]:
            await handler.handle_message(
                "tap/milo/accel/batch",
                json.dumps(b),
            )
        sess = handler.sessions[sid]
        assert sess.recover_from_jsonl is True
        assert len(sess.batches) == 2
        await handler.handle_message(
            "tap/milo/session",
            json.dumps(
                {
                    "event": "end_session",
                    "session_id": sid,
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                    "ts": time.time(),
                }
            ),
        )

    asyncio.run(run())
    meta = handler.get_session(sid)
    assert meta is not None
    assert meta["stopped"] is True
    assert "rebuilt_from_accel_jsonl" in meta["warnings"]
    assert abs(float(meta["t_start"]) - 100.0) < 1e-6
    assert meta["n_samples"] == 40
    npz = settings.session_store_dir / f"{sid}.npz"
    assert npz.is_file()
    with np.load(npz) as z:
        assert z["t"].size == 40
        assert abs(float(z["t"][0]) - 100.0) < 1e-6


def test_recover_open_sessions_from_recent_jsonl(tmp_path):
    sid = "live_open"
    settings = _settings(tmp_path)
    path = settings.telemetry_log_dir / sid / "accel.jsonl"
    _write_accel_jsonl(path, sid, [_batch(sid, 0, 1.0), _batch(sid, 1, 1.04)])
    # Touch mtime to "now"
    path.touch()
    handler = TapCollectorHandler(settings)
    n = handler.recover_open_sessions_from_logs()
    assert n == 1
    sess = handler.sessions[sid]
    assert sess.recover_from_jsonl is True
    assert sess.stopped is False
    assert sess.recording_state in ("recording", "stalled")


def test_recover_skips_stale_incomplete_jsonl(tmp_path):
    sid = "old_incomplete"
    settings = _settings(tmp_path, session_idle_timeout_s=30.0)
    path = settings.telemetry_log_dir / sid / "accel.jsonl"
    _write_accel_jsonl(path, sid, [_batch(sid, 0, 1.0)])
    # Age the file beyond live grace
    old = time.time() - 10_000
    os.utime(path, (old, old))
    handler = TapCollectorHandler(settings)
    assert handler.recover_open_sessions_from_logs() == 0
    assert sid not in handler.sessions
