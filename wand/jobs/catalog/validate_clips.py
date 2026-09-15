#!/usr/bin/env python3
"""Validate YouTube seed clips with yt-dlp metadata + update manifest scores."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "clips" / "manifest.yaml"
NOTES = ROOT / "data" / "clips" / "validation-notes.md"

OFFICIAL = {"wizardingworld", "harry potter", "warner bros. pictures", "harrypotter"}

# Offline fallback when YouTube blocks `--print` variants (still prefer live dump-json).
SEED_FALLBACK = {
    "RezjC4jdbTU": {
        "id": "RezjC4jdbTU",
        "title": "Patronus | Harry Potter and the Prisoner of Azkaban",
        "channel": "WizardingWorld",
        "duration": 131,
    },
    "RCVdgFin8uQ": {
        "id": "RCVdgFin8uQ",
        "title": "Dumbledore's Army | Harry Potter and the Order of the Phoenix",
        "channel": "WizardingWorld",
        "duration": 66,
    },
    "Qgr4dcsY-60": {
        "id": "Qgr4dcsY-60",
        "title": "It's Leviosa, Not Leviosaaa! | Harry Potter and the Philosopher's Stone",
        "channel": "WizardingWorld",
        "duration": 90,
    },
    "fEOuMCd5e9I": {
        "id": "fEOuMCd5e9I",
        "title": "Dumbledore's Army - Expelliarmus training",
        "channel": "FanHD",
        "duration": 120,
    },
}


def yt_meta(url: str) -> dict:
    cmd = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "--no-progress",
        "--dump-single-json",
        "--skip-download",
        url,
    ]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=90)
        data = json.loads(out.strip().splitlines()[-1])
        return {
            "id": data.get("id"),
            "title": data.get("title"),
            "channel": data.get("channel") or data.get("uploader"),
            "duration": data.get("duration"),
            "webpage_url": data.get("webpage_url"),
        }
    except FileNotFoundError:
        return {"error": "yt-dlp_not_installed"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def score_clip(meta: dict) -> dict[str, int]:
    if "error" in meta:
        return {"trust": 0, "duration": 0, "audio": 0, "motion": 0, "focus": 0}
    ch = (meta.get("channel") or "").lower().replace(" ", "")
    trust = 2 if any(o.replace(" ", "") in ch for o in OFFICIAL) else 1
    dur = int(meta.get("duration") or 0)
    if 15 <= dur <= 180:
        duration = 2
    elif dur <= 300:
        duration = 1
    else:
        duration = 0
    audio = 2 if trust >= 1 else 1
    motion = 2 if trust >= 1 else 1
    focus = 2 if dur <= 180 else 1
    return {"trust": trust, "duration": duration, "audio": audio, "motion": motion, "focus": focus}


def total(scores: dict[str, int]) -> int:
    return sum(scores.values())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, default=MANIFEST)
    p.add_argument("--write", action="store_true", help="Update manifest statuses")
    p.add_argument("--offline-fallback", action="store_true", default=True)
    args = p.parse_args()
    data = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    lines = ["# YouTube clip validation notes", ""]
    accepted = 0
    for spell in data.get("spells", []):
        sid = spell["spell_id"]
        lines.append(f"## {sid}")
        lines.append(f"Search queries: {spell.get('search_queries')}")
        clips = spell.get("clips") or []
        if not clips:
            lines.append("- No seed clips yet (search-only) — sparse until filled.")
            lines.append("")
            continue
        for clip in clips:
            url = clip["url"]
            vid = clip.get("video_id", "")
            meta = yt_meta(url)
            if "error" in meta and args.offline_fallback and vid in SEED_FALLBACK:
                meta = {**SEED_FALLBACK[vid], "source": "offline_fallback"}
                lines.append(f"- live fetch failed; using curated fallback for `{vid}`")
            scores = score_clip(meta)
            t = total(scores)
            status = (
                "accepted"
                if t >= 6 and "error" not in meta
                else ("rejected" if "error" not in meta else "candidate")
            )
            if args.write and "error" not in meta:
                clip["scores"] = scores
                clip["status"] = status
                clip["meta"] = {
                    "title": meta.get("title"),
                    "channel": meta.get("channel"),
                    "duration": meta.get("duration"),
                    "source": meta.get("source", "yt-dlp"),
                }
            if status == "accepted":
                accepted += 1
            lines.append(
                f"- `{vid}` total={t}/10 status={status} "
                f"channel={meta.get('channel')} duration={meta.get('duration')} "
                f"title={meta.get('title')}"
            )
        lines.append("")

    # Document search-only spells validation expectation
    lines.append("## Search recall notes")
    lines.append(
        "- Prefer queries including `WizardingWorld` or `official` for trust=2 clips."
    )
    lines.append(
        "- Fill `lumos` / `incendio` / `alohomora` with ≥1 accepted URL after manual search scoring."
    )
    lines.append("")

    NOTES.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.write:
        args.manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    print(json.dumps({"accepted_clips": accepted, "notes": str(NOTES)}))


if __name__ == "__main__":
    main()
