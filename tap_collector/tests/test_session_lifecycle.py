"""Idle finalize, force stop, and session delete/patch."""

from __future__ import annotations

import asyncio
import json
import time

from models.handler import TapCollectorHandler
from utils.config import Settings


def _settings(tmp_path, **kwargs):
    base = dict(
        session_store_dir=tmp_path / "sessions",
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
        session_idle_timeout_s=0.05,
        max_session_s=0.0,
    )
    base.update(kwargs)
    return Settings(**base)


def test_idle_finalize_empty_session(tmp_path):
    settings = _settings(tmp_path)
    handler = TapCollectorHandler(settings)

    async def run():
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps(
                {
                    "event": "start",
                    "session_id": "empty1",
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                    "ts": time.time(),
                }
            ),
        )
        sess = handler.sessions["empty1"]
        sess.updated_at = time.time() - 1.0
        await handler.sweep_idle()

    asyncio.run(run())
    meta = handler.get_session("empty1")
    assert meta is not None
    assert meta["stopped"] is True
    assert meta["recording_state"] == "stopped"
    assert "idle_timeout_empty" in meta["warnings"]
    assert (tmp_path / "sessions" / "empty1.json").is_file()
    assert not (tmp_path / "sessions" / "empty1.npz").is_file()


def test_force_stop_and_patch_delete(tmp_path):
    settings = _settings(tmp_path, session_idle_timeout_s=60.0)
    handler = TapCollectorHandler(settings)

    async def run():
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps(
                {
                    "event": "start",
                    "session_id": "live1",
                    "mode": "tap",
                    "sample_rate_hz": 100.0,
                }
            ),
        )
        stopped = await handler.force_stop_session("live1")
        assert stopped["already_stopped"] is False
        assert stopped["stopped"] is True

        again = await handler.force_stop_session("live1")
        assert again["already_stopped"] is True

        patched = await handler.patch_session(
            "live1", notes="keep for review", import_label="run-a"
        )
        assert patched["notes"] == "keep for review"
        assert patched["import_label"] == "run-a"
        disk = json.loads((tmp_path / "sessions" / "live1.json").read_text())
        assert disk["notes"] == "keep for review"

        deleted = await handler.delete_session("live1")
        assert deleted["deleted"] is True
        assert handler.get_session("live1") is None
        assert not (tmp_path / "sessions" / "live1.json").is_file()

    asyncio.run(run())


def test_delete_recording_requires_force(tmp_path):
    settings = _settings(tmp_path, session_idle_timeout_s=60.0)
    handler = TapCollectorHandler(settings)

    async def run():
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps({"event": "start", "session_id": "rec1", "mode": "tap"}),
        )
        try:
            await handler.delete_session("rec1", force=False)
            raise AssertionError("expected PermissionError")
        except PermissionError:
            pass
        deleted = await handler.delete_session("rec1", force=True)
        assert deleted["deleted"] is True
        assert handler.get_session("rec1") is None

    asyncio.run(run())


def test_max_session_timeout(tmp_path):
    settings = _settings(tmp_path, session_idle_timeout_s=3600.0, max_session_s=0.05)
    handler = TapCollectorHandler(settings)

    async def run():
        await handler.handle_message(
            "tap/dev1/session",
            json.dumps({"event": "start", "session_id": "max1", "mode": "tap"}),
        )
        sess = handler.sessions["max1"]
        sess.started_at = time.time() - 1.0
        sess.updated_at = time.time()  # still "active" by idle clock
        await handler.sweep_idle()

    asyncio.run(run())
    meta = handler.get_session("max1")
    assert meta is not None
    assert meta["stopped"] is True
    assert "max_session_timeout" in meta["warnings"]
