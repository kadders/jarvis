import json

import pytest

from models.handler import OximeterCollectorHandler
from utils.config import Settings


@pytest.fixture
def handler(tmp_path):
    settings = Settings(store_dir=tmp_path / "data")
    return OximeterCollectorHandler(settings)


@pytest.mark.asyncio
async def test_status_and_reading(handler):
    device = "aabbcc"
    await handler.handle_message(
        f"oximeter/{device}/status",
        json.dumps(
            {
                "online": True,
                "fw": "1.0.0",
                "ip": "192.168.1.50",
                "battery": 90,
                "valid": False,
            }
        ).encode(),
    )
    await handler.handle_message(
        f"oximeter/{device}/readings",
        json.dumps(
            {"ts": 1000, "hr": 72, "spo2": 98, "valid": True, "finger": True, "battery": 90}
        ).encode(),
    )
    devs = handler.list_devices()
    assert len(devs) == 1
    assert devs[0]["device_id"] == device
    assert devs[0]["ip"] == "192.168.1.50"
    sessions = handler.list_sessions(device_id=device)
    assert len(sessions) == 1
    assert sessions[0]["n_valid"] >= 1


@pytest.mark.asyncio
async def test_config_ack(handler):
    device = "dev1"
    await handler.handle_message(
        f"oximeter/{device}/config/ack",
        json.dumps({"ok": True}).encode(),
    )
    rec = handler.get_device(device)
    assert rec is not None
    assert rec.config.get("_last_ack", {}).get("ok") is True


def test_default_config_template(handler):
    tpl = handler.default_config_template("abc123")
    assert tpl["mqtt"]["prefix"] == "oximeter/abc123"
    assert tpl["v"] == 1


@pytest.mark.asyncio
async def test_session_analytics(handler):
    device = "aabbcc"
    await handler.handle_message(
        f"oximeter/{device}/readings",
        json.dumps(
            {"ts": 1000, "hr": 70, "spo2": 99, "valid": True, "finger": True}
        ).encode(),
    )
    await handler.handle_message(
        f"oximeter/{device}/readings",
        json.dumps(
            {"ts": 2000, "hr": 80, "spo2": 97, "valid": True, "finger": True}
        ).encode(),
    )
    sessions = handler.list_sessions(device_id=device)
    sid = sessions[0]["session_id"]
    stats = handler.session_analytics(sid)
    assert stats["n_valid"] == 2
    assert stats["hr"]["min"] == 70
    assert stats["hr"]["max"] == 80
