"""Resolve running job G-code paths against the read-only CAM NFS share."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.gcode_summary import summarize_gcode_file, sha256_file

_GCODE_EXTS = {".gcode", ".g", ".gc", ".nc", ".tap", ".cnc"}
_INDEX_TTL_S = 30.0
_MAX_WALK_DEPTH = 12


@dataclass
class CamResolveResult:
    status: str  # resolved | ambiguous | missing | disabled | too_large
    source_ref: str | None = None
    path: Path | None = None
    candidates: list[str] | None = None
    gcode_summary: dict[str, Any] | None = None
    gcode_sha256: str | None = None


def normalize_job_file(job_file: str | None) -> tuple[str | None, str | None]:
    """
    Normalize an RRF job path.

    Returns (basename, relative_after_gcodes) where relative may equal basename.
    Strips ``0:`` drive prefixes and a leading ``gcodes/`` segment (RRF host
    maps ``gcodes -> /mnt/CAM/`` = CAM share root).
    """
    if not job_file or not str(job_file).strip():
        return None, None
    raw = str(job_file).strip().replace("\\", "/")
    # Strip drive like 0: or 1:
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isalnum():
        raw = raw[2:]
    while raw.startswith("/"):
        raw = raw[1:]
    parts = [p for p in raw.split("/") if p and p != "."]
    if parts and parts[0].casefold() == "gcodes":
        parts = parts[1:]
    if not parts:
        return None, None
    # Reject traversal
    if any(p == ".." for p in parts):
        return None, None
    rel = "/".join(parts)
    return parts[-1], rel


class CamGcodeIndex:
    """Cached basename → relative paths index under a CAM share root."""

    def __init__(
        self,
        root: Path | None,
        *,
        ttl_s: float = _INDEX_TTL_S,
        max_depth: int = _MAX_WALK_DEPTH,
    ) -> None:
        self.root = Path(root) if root else None
        self.ttl_s = ttl_s
        self.max_depth = max_depth
        self._by_name: dict[str, list[str]] = {}
        self._built_at: float = 0.0

    def refresh_if_needed(self, *, force: bool = False) -> None:
        if self.root is None:
            return
        now = time.time()
        if not force and self._by_name and (now - self._built_at) < self.ttl_s:
            return
        by_name: dict[str, list[str]] = {}
        root = self.root
        if not root.is_dir():
            self._by_name = {}
            self._built_at = now
            return
        root_resolved = root.resolve()
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            rel_dir = Path(dirpath).relative_to(root)
            depth = len(rel_dir.parts) if str(rel_dir) != "." else 0
            if depth >= self.max_depth:
                dirnames[:] = []
                continue
            # Do not follow / descend into symlinks
            dirnames[:] = [
                d
                for d in dirnames
                if not (Path(dirpath) / d).is_symlink() and d not in (".", "..")
            ]
            for name in filenames:
                p = Path(dirpath) / name
                if p.is_symlink():
                    continue
                if p.suffix.lower() not in _GCODE_EXTS:
                    continue
                try:
                    # Ensure still under root
                    p.resolve().relative_to(root_resolved)
                except (OSError, ValueError):
                    continue
                rel = str(p.relative_to(root)).replace("\\", "/")
                key = name.casefold()
                by_name.setdefault(key, []).append(rel)
        self._by_name = by_name
        self._built_at = now

    def lookup_basename(self, basename: str) -> list[str]:
        self.refresh_if_needed()
        return list(self._by_name.get(basename.casefold(), []))


def resolve_cam_gcode(
    job_file: str | None,
    *,
    cam_dir: Path | None,
    index: CamGcodeIndex | None = None,
    gcode_sha256: str | None = None,
    max_bytes: int = 8 * 1024 * 1024,
) -> CamResolveResult:
    """
    Resolve ``job_file`` against the CAM share.

    Policy: unique basename → resolved; duplicates require matching sha256;
    otherwise ambiguous/missing. Never guesses by mtime.
    """
    if cam_dir is None:
        return CamResolveResult(status="disabled")
    root = Path(cam_dir)
    if not root.is_dir():
        return CamResolveResult(status="missing")

    basename, _rel = normalize_job_file(job_file)
    if not basename:
        return CamResolveResult(status="missing")

    idx = index or CamGcodeIndex(root)
    if idx.root is None or Path(idx.root) != root:
        idx = CamGcodeIndex(root)
    candidates = idx.lookup_basename(basename)
    if not candidates:
        return CamResolveResult(status="missing", candidates=[])

    if len(candidates) == 1:
        source_ref = candidates[0]
        path = root / source_ref
        summary = summarize_gcode_file(
            path,
            job_file=job_file,
            source_ref=source_ref,
            source="cam_share",
            gcode_resolution_status="resolved",
            max_bytes=max_bytes,
        )
        digest = (summary or {}).get("gcode_sha256")
        status = "resolved"
        if summary and summary.get("gcode_resolution_status") == "too_large":
            status = "too_large"
        return CamResolveResult(
            status=status,
            source_ref=source_ref,
            path=path if path.is_file() else None,
            candidates=candidates,
            gcode_summary=summary,
            gcode_sha256=digest,
        )

    # Ambiguous basename — require hash match
    if gcode_sha256:
        want = gcode_sha256.casefold()
        matches: list[str] = []
        for ref in candidates:
            digest = sha256_file(root / ref, max_bytes=max_bytes)
            if digest and digest.casefold() == want:
                matches.append(ref)
        if len(matches) == 1:
            source_ref = matches[0]
            path = root / source_ref
            summary = summarize_gcode_file(
                path,
                job_file=job_file,
                source_ref=source_ref,
                source="cam_share",
                gcode_resolution_status="resolved",
                max_bytes=max_bytes,
            )
            return CamResolveResult(
                status="resolved",
                source_ref=source_ref,
                path=path if path.is_file() else None,
                candidates=candidates,
                gcode_summary=summary,
                gcode_sha256=(summary or {}).get("gcode_sha256") or gcode_sha256,
            )

    return CamResolveResult(
        status="ambiguous",
        source_ref=None,
        path=None,
        candidates=candidates,
        gcode_summary={
            "job_file": job_file,
            "gcode_sha256": gcode_sha256,
            "source_ref": None,
            "source": "cam_share",
            "gcode_resolution_status": "ambiguous",
            "candidates": candidates,
            "n_lines": 0,
            "tools_used": [],
            "tool_change_lines": [],
            "spindle_commands": [],
            "feed_commands": [],
            "move_stats": {"n_g0": 0, "n_g1": 0},
            "operations": [],
        },
        gcode_sha256=gcode_sha256,
    )
