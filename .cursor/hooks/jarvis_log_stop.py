#!/usr/bin/env python3
"""stop: when an agent turn ends, post call + end result to Jarvis for training."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis_log_common import (  # noqa: E402
    emit_empty,
    post_feedback,
    read_stdin_json,
    truncate,
)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in ("text", "input_text", "output_text"):
                    parts.append(str(item.get("text") or ""))
                elif "text" in item:
                    parts.append(str(item.get("text") or ""))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return str(content.get("text") or content)
    return str(content)


def _extract_user_query(text: str) -> str:
    if "<user_query>" in text and "</user_query>" in text:
        return text.split("<user_query>", 1)[1].split("</user_query>", 1)[0].strip()
    return text.strip()


def _read_transcript(path: str) -> Tuple[str, str]:
    """Return (last_user_prompt, last_assistant_text) from a Cursor transcript JSONL."""
    last_user = ""
    last_assistant = ""
    p = Path(path)
    if not p.is_file():
        return "", ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", ""

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or obj.get("role")
        text = _content_to_text(msg.get("content"))
        if not text.strip():
            continue
        if role == "user":
            last_user = _extract_user_query(text)
        elif role == "assistant":
            # Prefer final prose; skip pure tool-call blobs when possible.
            if text.strip().startswith("{") and "tool_use" in text:
                continue
            last_assistant = text.strip()
    return last_user, last_assistant


def main() -> int:
    payload = read_stdin_json()
    status = str(payload.get("status") or "completed")
    if status == "aborted":
        emit_empty()
        return 0

    prompt, response = "", ""
    transcript_path = payload.get("transcript_path")
    if transcript_path:
        prompt, response = _read_transcript(str(transcript_path))

    if not prompt:
        prompt = (
            f"Cursor agent turn "
            f"(conversation_id={payload.get('conversation_id')}, "
            f"generation_id={payload.get('generation_id')})"
        )
    if not response:
        response = f"(no assistant transcript; status={status})"

    rating = 0
    if status == "error":
        rating = -1
    elif status == "completed":
        rating = 1

    meta: Dict[str, Any] = {
        "kind": "outcome",
        "hook": "stop",
        "status": status,
        "model": payload.get("model"),
        "conversation_id": payload.get("conversation_id"),
        "generation_id": payload.get("generation_id"),
        "loop_count": payload.get("loop_count"),
        "logic": truncate(
            f"Agent stop status={status} model={payload.get('model')}",
            2000,
        ),
    }

    preferred = response if rating > 0 else None
    ok, detail = post_feedback(
        prompt=prompt,
        response=response,
        rating=rating,
        source="cursor",
        metadata=meta,
        preferred=preferred,
    )
    if not ok:
        print(f"jarvis-log-stop: {detail}", file=sys.stderr)
    else:
        print(f"jarvis-log-stop: logged status={status}", file=sys.stderr)

    emit_empty()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
