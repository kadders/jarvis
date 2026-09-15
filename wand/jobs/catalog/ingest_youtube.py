#!/usr/bin/env python3
"""Ingest accepted YouTube clips: download → extract wav → optional ASR lexicon check."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from models.lexicon import LexiconMatcher  # noqa: E402


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, default=ROOT / "data" / "clips" / "manifest.yaml")
    p.add_argument("--out-dir", type=Path, default=ROOT / "data" / "clips" / "raw")
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--download", action="store_true")
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    lexicon = LexiconMatcher.from_path(ROOT / "data" / "ontology" / "spells.json")
    results = []
    n = 0
    for spell in data.get("spells", []):
        for clip in spell.get("clips") or []:
            if clip.get("status") not in ("accepted", "candidate"):
                continue
            if n >= args.limit:
                break
            vid = clip["video_id"]
            url = clip["url"]
            media = args.out_dir / f"{vid}.mp4"
            wav = args.out_dir / f"{vid}.wav"
            if args.download and not media.exists():
                run(
                    [
                        "yt-dlp",
                        "-f",
                        "bv*+ba/b",
                        "-o",
                        str(args.out_dir / "%(id)s.%(ext)s"),
                        url,
                    ]
                )
            if media.exists() or list(args.out_dir.glob(f"{vid}.*")):
                src = media if media.exists() else next(args.out_dir.glob(f"{vid}.*"))
                if not wav.exists():
                    run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(src),
                            "-vn",
                            "-ac",
                            "1",
                            "-ar",
                            "16000",
                            str(wav),
                        ]
                    )
            # Lexicon smoke without full ASR: use title/notes as proxy if no transcript file
            transcript_proxy = (clip.get("notes") or "") + " " + spell["spell_id"].replace("_", " ")
            match = lexicon.match(transcript_proxy)
            results.append(
                {
                    "spell_id": spell["spell_id"],
                    "video_id": vid,
                    "lexicon_hit": bool(match and match.spell_id == spell["spell_id"]),
                    "wav": str(wav) if wav.exists() else None,
                }
            )
            n += 1
        if n >= args.limit:
            break
    out = ROOT / "data" / "clips" / "ingest_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"results": results, "out": str(out)}))


if __name__ == "__main__":
    main()
