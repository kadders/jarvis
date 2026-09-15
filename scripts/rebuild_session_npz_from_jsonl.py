#!/usr/bin/env python3
"""Rebuild a tap-collector session NPZ (+ JSON bounds) from telemetry accel.jsonl.

Usage (from jarvis repo, with deps installed):

  cd /media/kad/data/repositories/jarvis
  .venv/bin/python scripts/rebuild_session_npz_from_jsonl.py \\
    --session-id 20260906_125018 \\
    --session-dir /media/kad/data/k8s/jarvis-llm/tap-sessions \\
    --telemetry-dir /media/kad/data/k8s/jarvis-llm/tap-sessions/telemetry

Keeps a backup of any existing truncated NPZ as ``*.npz.bak_truncated``.
If the live NPZ is not writable (container-owned), writes ``*.npz.rebuilt`` and
``*.json.rebuilt`` for install via kubectl cp into the tap-collector pod.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

# Allow running from repo without install
_TAP_COLLECTOR = Path(__file__).resolve().parents[1] / "tap_collector"
if str(_TAP_COLLECTOR) not in sys.path:
    sys.path.insert(0, str(_TAP_COLLECTOR))

from models.import_csv import save_timeseries_npz  # noqa: E402
from models.replay import peek_accel_jsonl_meta, reassemble_accel_jsonl  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session-id", required=True)
    ap.add_argument(
        "--session-dir",
        type=Path,
        default=Path("/media/kad/data/k8s/jarvis-llm/tap-sessions"),
    )
    ap.add_argument(
        "--telemetry-dir",
        type=Path,
        default=None,
        help="Defaults to {session-dir}/telemetry",
    )
    ap.add_argument("--sample-rate-hz", type=float, default=800.0)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Reassemble and print stats without writing",
    )
    args = ap.parse_args()
    sid = args.session_id
    session_dir = args.session_dir
    telemetry_dir = args.telemetry_dir or (session_dir / "telemetry")
    jsonl = telemetry_dir / sid / "accel.jsonl"
    if not jsonl.is_file():
        print(f"missing accel.jsonl: {jsonl}", file=sys.stderr)
        return 1

    first = peek_accel_jsonl_meta(jsonl)
    print(f"jsonl={jsonl} size={jsonl.stat().st_size} first={first}")
    t, data, sr, n_batches = reassemble_accel_jsonl(
        jsonl, sample_rate_hz=args.sample_rate_hz, session_id=sid
    )
    if t.size == 0:
        print("no samples reassembled", file=sys.stderr)
        return 1
    print(
        f"reassembled batches={n_batches} samples={t.size} "
        f"t=[{float(t[0]):.3f}, {float(t[-1]):.3f}] sr={sr}"
    )

    npz_path = session_dir / f"{sid}.npz"
    json_path = session_dir / f"{sid}.json"
    if args.dry_run:
        if npz_path.is_file():
            with np.load(npz_path) as z:
                old_t = np.asarray(z["t"])
            print(
                f"existing NPZ samples={old_t.size} "
                f"t=[{float(old_t[0]):.3f}, {float(old_t[-1]):.3f}]"
            )
        return 0

    if npz_path.is_file():
        bak = session_dir / f"{sid}.npz.bak_truncated"
        if not bak.is_file():
            shutil.copy2(npz_path, bak)
            print(f"backed up truncated NPZ → {bak}")
        else:
            print(f"keeping existing backup {bak}")

    # Always write a user-owned rebuilt file first (session PVC files are often
    # owned by the container uid and not writable from the host).
    rebuilt_npz = session_dir / f"{sid}.npz.rebuilt"
    if rebuilt_npz.exists():
        rebuilt_npz.unlink()
    save_timeseries_npz(rebuilt_npz, t, data, sr)
    wrote_npz = rebuilt_npz
    print(f"wrote {rebuilt_npz}")
    try:
        # Best-effort in-place replace when permissions allow
        rebuilt_npz.replace(npz_path)
        wrote_npz = npz_path
        # replace consumed rebuilt path; copy back so we still have a local artifact
        shutil.copy2(npz_path, rebuilt_npz)
        print(f"installed {npz_path}")
    except OSError as e:
        print(f"left rebuilt NPZ for kubectl install ({e})", file=sys.stderr)

    meta: dict = {}
    if json_path.is_file():
        meta = json.loads(json_path.read_text(encoding="utf-8"))
    meta["session_id"] = sid
    meta["t_start"] = float(t[0])
    meta["t_end"] = float(t[-1])
    meta["n_samples"] = int(t.size)
    meta["sample_rate_hz"] = float(sr)
    meta["has_timeseries"] = True
    meta["n_batches"] = int(n_batches)
    warnings = list(meta.get("warnings") or [])
    if "rebuilt_from_accel_jsonl" not in warnings:
        warnings.append("rebuilt_from_accel_jsonl")
    notes = str(meta.get("notes") or "")
    note = (
        "NPZ rebuilt from telemetry accel.jsonl after collector restart truncated "
        "in-memory session; video may still be short if tap-stream ffmpeg SIGBUS."
    )
    if note not in notes:
        meta["notes"] = (notes + "\n" + note).strip() if notes else note
    meta["warnings"] = warnings
    meta_text = json.dumps(meta, indent=2)
    try:
        json_path.write_text(meta_text, encoding="utf-8")
        print(f"updated {json_path}")
    except PermissionError:
        alt_json = session_dir / f"{sid}.json.rebuilt"
        alt_json.write_text(meta_text, encoding="utf-8")
        print(
            f"cannot overwrite {json_path} (permission); wrote {alt_json}",
            file=sys.stderr,
        )
        print(
            "Install into the pod:\n"
            f"  kubectl -n jarvis-llm cp {wrote_npz} "
            f"deploy/tap-collector:/app/cache/tap_sessions/{sid}.npz\n"
            f"  kubectl -n jarvis-llm cp {alt_json} "
            f"deploy/tap-collector:/app/cache/tap_sessions/{sid}.json"
        )
        return 0
    if wrote_npz != npz_path:
        print(
            "Install NPZ into the pod:\n"
            f"  kubectl -n jarvis-llm cp {wrote_npz} "
            f"deploy/tap-collector:/app/cache/tap_sessions/{sid}.npz"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
