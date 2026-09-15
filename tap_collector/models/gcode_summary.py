"""Lightweight G-code summary parser (ops foundation; not a full CAM interpreter)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

# Word letter + number (optional sign/decimal), e.g. T3, G0, F1350, S18000, M3
_WORD_RE = re.compile(r"([A-Za-z])\s*([-+]?\d*\.?\d+)")
_COMMENT_PAREN_RE = re.compile(r"\([^)]*\)")

# Cap lists shipped in session JSON / MQTT
_MAX_TOOL_CHANGES = 200
_MAX_SPINDLE_ENTRIES = 40
_MAX_FEED_ENTRIES = 40
_MAX_OPS = 50

DEFAULT_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> str | None:
    """Hash a file up to max_bytes; return None if missing/unreadable/oversized."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > max_bytes:
        return None
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None


def summarize_gcode_text(
    text: str,
    *,
    job_file: str | None = None,
    gcode_sha256: str | None = None,
    source_ref: str | None = None,
    source: str | None = None,
    gcode_resolution_status: str | None = None,
) -> dict[str, Any]:
    """
    Parse a G-code program into a compact ops summary.

    Preserves tool-change byte offsets for later file_position correlation.
    Does not simulate path geometry.
    """
    tools_used: list[int] = []
    tools_seen: set[int] = set()
    tool_change_lines: list[dict[str, Any]] = []
    spindle_cmds: dict[tuple[str, float | None], int] = {}
    feed_cmds: dict[float, int] = {}
    ops: list[str] = []
    n_g0 = 0
    n_g1 = 0
    n_lines = 0
    byte_offset = 0
    # Track last newline encoding as original text may mix; use utf-8 encode of each line.
    encoded = text.encode("utf-8", errors="replace")
    # Walk lines with byte offsets matching encoded form when possible
    pos = 0
    for raw_line in text.splitlines(keepends=True):
        line_bytes = raw_line.encode("utf-8", errors="replace")
        line_start = pos
        pos += len(line_bytes)
        # Strip keepends for parsing
        line = raw_line.rstrip("\r\n")
        n_lines += 1
        code = _strip_comments(line).strip()
        if not code:
            # Still capture OPERATION-style comments from original
            op = _operation_marker(line)
            if op and len(ops) < _MAX_OPS:
                ops.append(op)
            continue
        op = _operation_marker(line)
        if op and len(ops) < _MAX_OPS:
            ops.append(op)

        words = {m.group(1).upper(): m.group(2) for m in _WORD_RE.finditer(code)}
        if "T" in words:
            try:
                tn = int(float(words["T"]))
            except (TypeError, ValueError):
                tn = None
            if tn is not None and tn >= 0:
                if tn not in tools_seen:
                    tools_seen.add(tn)
                    tools_used.append(tn)
                if len(tool_change_lines) < _MAX_TOOL_CHANGES:
                    tool_change_lines.append(
                        {
                            "line": n_lines,
                            "byte_offset": line_start,
                            "tool_number": tn,
                        }
                    )
        if "M" in words:
            try:
                mnum = int(float(words["M"]))
            except (TypeError, ValueError):
                mnum = None
            if mnum in (3, 4, 5):
                s_val: float | None = None
                if "S" in words:
                    try:
                        s_val = float(words["S"])
                    except (TypeError, ValueError):
                        s_val = None
                key = (f"M{mnum}", s_val)
                spindle_cmds[key] = spindle_cmds.get(key, 0) + 1
        elif "S" in words:
            try:
                s_val = float(words["S"])
            except (TypeError, ValueError):
                s_val = None
            if s_val is not None:
                key = ("S", s_val)
                spindle_cmds[key] = spindle_cmds.get(key, 0) + 1
        if "F" in words:
            try:
                f_val = float(words["F"])
            except (TypeError, ValueError):
                f_val = None
            if f_val is not None and f_val > 0:
                feed_cmds[f_val] = feed_cmds.get(f_val, 0) + 1
        if "G" in words:
            try:
                gnum = int(float(words["G"]))
            except (TypeError, ValueError):
                gnum = None
            if gnum == 0:
                n_g0 += 1
            elif gnum == 1:
                n_g1 += 1

    del encoded  # length taken from walk; hash computed by caller
    spindle_list = [
        {"cmd": cmd, "s": s, "count": cnt}
        for (cmd, s), cnt in sorted(
            spindle_cmds.items(), key=lambda x: (-x[1], x[0][0], x[0][1] or 0)
        )[:_MAX_SPINDLE_ENTRIES]
    ]
    feed_list = [
        {"f": f, "count": cnt}
        for f, cnt in sorted(feed_cmds.items(), key=lambda x: (-x[1], x[0]))[
            :_MAX_FEED_ENTRIES
        ]
    ]
    out: dict[str, Any] = {
        "job_file": job_file,
        "gcode_sha256": gcode_sha256,
        "source_ref": source_ref,
        "source": source,
        "gcode_resolution_status": gcode_resolution_status,
        "n_lines": n_lines,
        "n_bytes": pos,
        "tools_used": tools_used,
        "tool_change_lines": tool_change_lines,
        "spindle_commands": spindle_list,
        "feed_commands": feed_list,
        "move_stats": {"n_g0": n_g0, "n_g1": n_g1},
        "operations": ops,
    }
    return out


def summarize_gcode_file(
    path: Path,
    *,
    job_file: str | None = None,
    source_ref: str | None = None,
    source: str = "cam_share",
    gcode_resolution_status: str = "resolved",
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any] | None:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return {
                "job_file": job_file,
                "gcode_sha256": None,
                "source_ref": source_ref,
                "source": source,
                "gcode_resolution_status": "too_large",
                "n_lines": 0,
                "n_bytes": size,
                "tools_used": [],
                "tool_change_lines": [],
                "spindle_commands": [],
                "feed_commands": [],
                "move_stats": {"n_g0": 0, "n_g1": 0},
                "operations": [],
                "warnings": ["gcode_too_large"],
            }
        data = path.read_bytes()
    except OSError:
        return None
    digest = sha256_bytes(data)
    text = data.decode("utf-8", errors="replace")
    return summarize_gcode_text(
        text,
        job_file=job_file,
        gcode_sha256=digest,
        source_ref=source_ref,
        source=source,
        gcode_resolution_status=gcode_resolution_status,
    )


def summarize_gcode_bytes(
    data: bytes,
    *,
    job_file: str | None = None,
    source: str = "rrf_download",
    gcode_resolution_status: str = "rrf_download",
) -> dict[str, Any]:
    digest = sha256_bytes(data)
    text = data.decode("utf-8", errors="replace")
    return summarize_gcode_text(
        text,
        job_file=job_file,
        gcode_sha256=digest,
        source_ref=None,
        source=source,
        gcode_resolution_status=gcode_resolution_status,
    )


def _strip_comments(line: str) -> str:
    # ; comment to EOL
    if ";" in line:
        line = line.split(";", 1)[0]
    return _COMMENT_PAREN_RE.sub("", line)


def _operation_marker(line: str) -> str | None:
    stripped = line.strip()
    upper = stripped.upper()
    if upper.startswith(";OPERATION:") or upper.startswith("; OPERATION:"):
        return stripped[:120]
    if upper.startswith("(OPERATION:"):
        return stripped[:120]
    if upper.startswith(";TOOL") or upper.startswith("; TOOL"):
        return stripped[:120]
    return None
