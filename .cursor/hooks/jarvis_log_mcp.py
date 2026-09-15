#!/usr/bin/env python3
"""afterMCPExecution: mirror jarvis MCP call + result into the feedback store."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow importing sibling helper when Cursor runs this as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis_log_common import (  # noqa: E402
    SKIP_MCP_TOOLS,
    emit_empty,
    extract_event_id,
    infer_domain_from_text,
    post_feedback,
    read_stdin_json,
    tool_basename,
    truncate,
    MAX_LOGIC,
)


def main() -> int:
    payload = read_stdin_json()
    tool = tool_basename(str(payload.get("tool_name") or ""))
    if not tool or tool in SKIP_MCP_TOOLS:
        emit_empty()
        return 0

    # Only auto-log Jarvis MCP tools (avoid flooding from other MCP servers).
    if not tool.startswith("jarvis_"):
        emit_empty()
        return 0

    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        call = json.dumps(tool_input, ensure_ascii=False)
    else:
        call = str(tool_input or "")

    result = str(payload.get("result_json") or payload.get("result") or "")
    if not call.strip() and not result.strip():
        emit_empty()
        return 0

    duration = payload.get("duration")
    meta = {
        "kind": "outcome",
        "hook": "afterMCPExecution",
        "tool_name": tool,
    }
    dom = infer_domain_from_text(f"{call}\n{result}")
    if dom:
        meta["domain"] = dom
    if duration is not None:
        meta["duration_ms"] = duration

    logic = f"MCP tool {tool} completed"
    if duration is not None:
        logic += f" in {duration}ms"

    event_id = extract_event_id(result)
    ok, detail = post_feedback(
        prompt=f"[MCP {tool}] {call}",
        response=result or "(empty result)",
        rating=0,
        source="cursor",
        metadata=meta,
        event_id=event_id,
    )
    if not ok:
        print(f"jarvis-log-mcp: {detail}", file=sys.stderr)
    else:
        # Keep logic field available for operators reading stderr Hooks channel.
        print(f"jarvis-log-mcp: logged {tool} ({truncate(logic, MAX_LOGIC)})", file=sys.stderr)

    emit_empty()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
