#!/usr/bin/env python3
"""Shared helpers for Cursor hooks that post call+result trajectories to Jarvis."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

# Tools that already write feedback — skip to avoid recursion / noise.
SKIP_MCP_TOOLS = frozenset(
    {
        "jarvis_log_outcome",
        "jarvis_submit_feedback",
        "jarvis_feedback_stats",
        "jarvis_health",
        "jarvis_get_event_status",
        "jarvis_monitor_start",
        "jarvis_monitor_stop",
        "jarvis_ocr_health",
    }
)

MAX_PROMPT = 12000
MAX_RESPONSE = 12000
MAX_LOGIC = 8000


def feedback_url() -> str:
    base = os.environ.get(
        "JARVIS_LLM_BASE_URL",
        "http://llm.jarvis.lan/api/v1",
    ).rstrip("/")
    return f"{base}/feedback"


def truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...[truncated]..."


def post_feedback(
    *,
    prompt: str,
    response: str,
    rating: int = 0,
    source: str = "cursor",
    metadata: Optional[Dict[str, Any]] = None,
    preferred: Optional[str] = None,
    correction: Optional[str] = None,
    event_id: Optional[str] = None,
    completion_id: Optional[str] = None,
    timeout_s: float = 8.0,
) -> Tuple[bool, str]:
    body: Dict[str, Any] = {
        "rating": int(rating),
        "source": source if source in ("ui", "cursor", "mcp", "api", "other") else "cursor",
        "prompt": truncate(prompt, MAX_PROMPT),
        "response": truncate(response, MAX_RESPONSE),
        "metadata": metadata or {},
    }
    if preferred:
        body["preferred"] = truncate(preferred, MAX_RESPONSE)
    if correction:
        body["correction"] = truncate(correction, MAX_RESPONSE)
    if event_id:
        body["event_id"] = event_id
    if completion_id:
        body["completion_id"] = completion_id

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        feedback_url(),
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return True, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        return False, f"http_{e.code}: {detail}"
    except Exception as e:  # noqa: BLE001 — fail open in hooks
        return False, f"{type(e).__name__}: {e}"


def extract_event_id(result_text: str) -> Optional[str]:
    try:
        obj = json.loads(result_text)
    except Exception:
        return None
    if isinstance(obj, dict):
        eid = obj.get("event_id")
        if eid:
            return str(eid)
        # Events API sometimes nests under response
        resp = obj.get("response")
        if isinstance(resp, dict) and resp.get("event_id"):
            return str(resp["event_id"])
    return None


def tool_basename(tool_name: str) -> str:
    name = (tool_name or "").strip()
    # Cursor may send "MCP: jarvis_ask_local" or "jarvis_ask_local"
    if ":" in name:
        name = name.split(":", 1)[-1].strip()
    return name


def infer_domain_from_text(text: str) -> Optional[str]:
    t = (text or "").lower()
    if any(k in t for k in (".cpp", "esp32", "platformio", "arduino", "fastled", "wifimode")):
        return "arduino_wand"
    if any(
        k in t
        for k in (
            "mentra",
            "mentraglass",
            "@mentra/miniapp",
            "@mentra/bluetooth-sdk",
            "registerminiapp",
            "devicemodels.mentralive",
            "mentra live",
            "mentraos",
            "session.display.render",
            "create-mentra-miniapp",
        )
    ):
        return "mentra"
    if any(
        k in t
        for k in (
            "fusion 360",
            "fusion360",
            "create_box",
            "get_scene_info",
            "chipload",
            "feed_mm_min",
            "post-process.number",
            "manufacture",
            "toolpath",
            "adaptive clearing",
            "cutting_force",
            "fusion mcp",
        )
    ):
        return "cad"
    if any(k in t for k in ("reprapfirmware", "rrf", "nxt", "gcode", "inputs[state.thisinput]")):
        return "rrf_nxt"
    if any(k in t for k in (".py", "pytest", "fastapi", "black", "python", "mqtt", "jarvis")):
        return "python_jarvis"
    return None


def read_stdin_json() -> Dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


def emit_empty() -> None:
    print("{}")
