#!/usr/bin/env python3
"""Repair tool timeline + motion + segments for a tap-collector session.

Use when finalize rebuilt ADXL from accel.jsonl but lost early tools/motion
(collector restart mid-job before tool.jsonl existed).

Example:

  cd /media/kad/data/repositories/jarvis
  .venv/bin/python scripts/repair_session_tools_motion.py \\
    --session-id 20260907_150821 \\
    --session-dir /media/kad/data/k8s/jarvis-llm/tap-sessions \\
    --telemetry-dir /media/kad/data/k8s/jarvis-llm/tap-sessions/telemetry
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

_TAP_COLLECTOR = Path(__file__).resolve().parents[1] / "tap_collector"
if str(_TAP_COLLECTOR) not in sys.path:
    sys.path.insert(0, str(_TAP_COLLECTOR))

from models.handler import TapCollectorHandler  # noqa: E402
from utils.config import Settings  # noqa: E402


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
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Restore in a temp copy of session JSON; do not overwrite",
    )
    args = ap.parse_args()
    sid = args.session_id
    session_dir = args.session_dir
    telemetry_dir = args.telemetry_dir or (session_dir / "telemetry")
    json_path = session_dir / f"{sid}.json"
    if not json_path.is_file() and not (telemetry_dir / sid).is_dir():
        print(f"missing session artifacts for {sid}", file=sys.stderr)
        return 1

    # Always work in a writable temp tree (PVC files are often container-owned).
    tmp = Path(tempfile.mkdtemp(prefix=f"repair_{sid}_"))
    work_dir = tmp
    if json_path.is_file():
        shutil.copy2(json_path, work_dir / f"{sid}.json")
    npz = session_dir / f"{sid}.npz"
    if npz.is_file():
        shutil.copy2(npz, work_dir / f"{sid}.npz")
    mqtt_src = session_dir / "mqtt_events"
    if mqtt_src.is_dir():
        # Only copy day files that mention this session (faster / smaller).
        mqtt_dst = work_dir / "mqtt_events"
        mqtt_dst.mkdir(parents=True, exist_ok=True)
        for day in sorted(mqtt_src.glob("????-??-??.jsonl")):
            try:
                # Quick filter: copy if session id appears anywhere in file.
                if sid.encode() in day.read_bytes():
                    shutil.copy2(day, mqtt_dst / day.name)
            except OSError:
                continue
    print(f"work dir: {work_dir}")

    settings = Settings(
        session_store_dir=work_dir,
        telemetry_log_dir=telemetry_dir,
        llm_forward_enabled=False,
        publish_jarvis_analysis=False,
    )
    handler = TapCollectorHandler(settings)

    async def run() -> dict:
        return await handler.replay_session_from_log(
            sid,
            restore_tools=True,
            restore_motion=True,
            reanalyze=True,
        )

    result = asyncio.run(run())
    print(json.dumps(result, indent=2))

    out_json = work_dir / f"{sid}.json"
    if not out_json.is_file():
        print("repair produced no session JSON", file=sys.stderr)
        return 1

    data = json.loads(out_json.read_text(encoding="utf-8"))
    tools = sorted(
        {
            e.get("tool_number")
            for e in (data.get("tool_events") or [])
            if e.get("tool_number") is not None
        }
    )
    segs = [
        (s.get("tool_number"), s.get("t0"), s.get("t1"))
        for s in (data.get("segments") or [])
    ]
    print(
        f"repaired tools={data.get('n_tool_events')} motion={data.get('n_motion_samples')} "
        f"segments={data.get('n_segments')} tool_numbers={tools}"
    )
    for seg in segs:
        print(f"  segment tool={seg[0]} t=[{seg[1]}, {seg[2]}]")

    if args.dry_run:
        print(f"dry-run only; left artifacts in {work_dir}")
        return 0 if "error" not in result else 1

    rebuilt = session_dir / f"{sid}.json.rebuilt"
    rebuilt.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote {rebuilt}")

    installed = False
    try:
        # Best-effort in-place when host can write the PVC file.
        bak = session_dir / f"{sid}.json.bak_pre_tool_repair"
        if json_path.is_file() and not bak.is_file():
            try:
                shutil.copy2(json_path, bak)
            except OSError:
                pass
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        installed = True
        print(f"installed {json_path}")
    except OSError as e:
        print(f"cannot overwrite {json_path} ({e})", file=sys.stderr)
        print(
            "Install into the pod:\n"
            f"  kubectl -n jarvis-llm cp {rebuilt} "
            f"deploy/tap-collector:/app/cache/tap_sessions/{sid}.json"
        )

    return 0 if ("error" not in result and (installed or rebuilt.is_file())) else 1


if __name__ == "__main__":
    raise SystemExit(main())
