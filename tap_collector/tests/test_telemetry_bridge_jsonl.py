"""Telemetry bridge durable JSONL leaf coverage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Bridge lives outside tap_collector package
_BRIDGE = Path(__file__).resolve().parents[2] / "telemetry_bridge"
if str(_BRIDGE) not in sys.path:
    sys.path.insert(0, str(_BRIDGE))


def test_bridge_writes_tool_and_session_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRY_BRIDGE_LOG_DIR", str(tmp_path / "telemetry"))
    monkeypatch.setenv("TELEMETRY_BRIDGE_KAFKA_BOOTSTRAP", "")
    import main as bridge_main

    b = bridge_main.TelemetryBridge()
    sid = "sess1"
    b._handle_message(
        "tap/milo/tool",
        json.dumps({"session_id": sid, "tool_number": 13, "event": "change"}).encode(),
    )
    b._handle_message(
        "tap/milo/session",
        json.dumps({"session_id": sid, "event": "start_session", "tool_number": 13}).encode(),
    )
    b._handle_message(
        "tap/milo/motion",
        json.dumps({"session_id": sid, "t_s": 1.0, "x": 0, "y": 0, "z": 0}).encode(),
    )
    b._handle_message(
        "tap/milo/spindle",
        json.dumps(
            {
                "session_id": sid,
                "t_s": 1.5,
                "rpm": 18000,
                "source": "arborctl",
                "commanded_rpm": 18000,
            }
        ).encode(),
    )
    assert (tmp_path / "telemetry" / sid / "tool.jsonl").is_file()
    assert (tmp_path / "telemetry" / sid / "session.jsonl").is_file()
    assert (tmp_path / "telemetry" / sid / "motion.jsonl").is_file()
    assert (tmp_path / "telemetry" / sid / "spindle.jsonl").is_file()
    tool_line = (tmp_path / "telemetry" / sid / "tool.jsonl").read_text().strip()
    assert json.loads(tool_line)["payload"]["tool_number"] == 13
    spindle_line = (tmp_path / "telemetry" / sid / "spindle.jsonl").read_text().strip()
    assert json.loads(spindle_line)["payload"]["rpm"] == 18000
