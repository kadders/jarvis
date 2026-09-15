#!/usr/bin/env python3
"""
Build an Arduino / ESP32 coding preference dataset.

Sources:
  - training/datasets/arduino_wand/STYLE.md
  - wand/edge/esp32/
  - tiny-ocr/firmware/src/ + hardware docs

Writes preferences.jsonl + instructions.jsonl for training.train_dpo.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from training.dataset_common import (
    Instruction,
    Preference,
    dedupe_prefs,
    extract_code_md_pairs,
    make_fence_re,
    write_jsonl,
)

LICENSE = "proprietary"
CPP_FENCE = make_fence_re("cpp")


def _prompt_arduino(topic: str, _chosen: str) -> str:
    return (
        f"You are writing Arduino/ESP32 firmware for Jarvis edge devices (PlatformIO). "
        f"Produce the CORRECT form for: {topic}. "
        "Prefer non-blocking loops, sized JsonDocument, and http.end()."
    )


def _synthetic_reject_cpp(chosen: str) -> str:
    out = chosen
    out = re.sub(r"millis\(\)", "delay(500)", out, count=1)
    out = re.sub(r"http\.end\(\);", "", out)
    out = re.sub(r"JsonDocument", "StaticJsonDocument<64>", out)
    return out


def extract_style_md(style_path: Path) -> Tuple[List[Preference], List[Instruction]]:
    text = style_path.read_text(encoding="utf-8", errors="replace")
    return extract_code_md_pairs(
        text,
        str(style_path.name),
        kind="arduino_style",
        license_=LICENSE,
        tags=["STYLE.md", "arduino"],
        fence_re=CPP_FENCE,
        prompt_fn=_prompt_arduino,
        synthetic_reject_fn=_synthetic_reject_cpp,
    )


def extract_cpp_snippets(
    cpp_path: Path,
    root: Path,
    *,
    kind: str = "esp32_wifi",
    max_snippets: int = 6,
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    try:
        text = cpp_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return prefs, instr
    rel = str(cpp_path.relative_to(root))
    patterns = [
        (r"void\s+loop\s*\([^)]*\)\s*\{[^}]+\}", "non_blocking", "ESP32 main loop"),
        (r"WiFi\.begin[^;]+;", "esp32_wifi", "WiFi setup"),
        (r"HTTPClient\s+http[^;]+;[\s\S]{0,400}http\.GET\(\)", "http_json", "HTTP poll"),
        (r"deserializeJson\([^)]+\)", "http_json", "JSON parse"),
    ]
    count = 0
    for pattern, skind, label in patterns:
        m = re.search(pattern, text, re.MULTILINE)
        if not m:
            continue
        snippet = m.group(0).strip()
        if len(snippet) < 30:
            continue
        prompt = f"Review this {label} snippet from {rel}. Show the improved ESP32 pattern."
        chosen = snippet
        rejected = _synthetic_reject_cpp(snippet)
        if rejected.strip() == chosen.strip():
            continue
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind=skind,
                source_path=rel,
                license=LICENSE,
                tags=[label],
            )
        )
        count += 1
        if count >= max_snippets:
            break
    return prefs, instr


def extract_platformio_pairs(ini_path: Path) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    if not ini_path.is_file():
        return prefs, instr
    text = ini_path.read_text(encoding="utf-8", errors="replace")
    prompt = "What framework and board should Wand ESP32 edge firmware use in platformio.ini?"
    chosen = "framework = arduino\nboard = seeed_xiao_esp32s3 (or project board)"
    rejected = "framework = espidf only with no ArduinoJson/FastLED deps"
    if "arduino" in text.lower():
        chosen = text[:800] if len(text) > 800 else text
    prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
    instr.append(
        Instruction(
            prompt=prompt,
            response=chosen,
            kind="platformio",
            source_path=str(ini_path.name),
            license=LICENSE,
            tags=["platformio"],
        )
    )
    return prefs, instr


def extract_wiring_qa(wiring_path: Path) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    if not wiring_path.is_file():
        return prefs, instr
    text = wiring_path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"(I2C|SDA|SCL|GPIO)\s*[:=]?\s*(\d+)", text, re.IGNORECASE):
        signal, pin = m.group(1), m.group(2)
        prompt = f"Which pin is used for {signal} on the tiny-ocr hardware?"
        chosen = f"{signal} on GPIO {pin} (see hardware/wiring.md)."
        rejected = f"Use GPIO 99 for {signal}; wiring doc is optional."
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="peripheral",
                source_path="hardware/wiring.md",
                license=LICENSE,
                tags=["wiring", signal.lower()],
            )
        )
    return prefs[:8], instr[:8]


def build_dataset(jarvis_root: Path, tiny_ocr_root: Path) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []

    style = jarvis_root / "training" / "datasets" / "arduino_wand" / "STYLE.md"
    if style.is_file():
        p, i = extract_style_md(style)
        prefs.extend(p)
        instr.extend(i)

    esp32 = jarvis_root / "wand" / "edge" / "esp32"
    main_cpp = esp32 / "src" / "main.cpp"
    if main_cpp.is_file():
        p, i = extract_cpp_snippets(main_cpp, jarvis_root)
        prefs.extend(p)
        instr.extend(i)
    p, i = extract_platformio_pairs(esp32 / "platformio.ini")
    prefs.extend(p)
    instr.extend(i)

    fw = tiny_ocr_root / "firmware" / "src"
    if fw.is_dir():
        for cpp in sorted(fw.glob("*.cpp"))[:6]:
            p, i = extract_cpp_snippets(cpp, tiny_ocr_root)
            prefs.extend(p)
            instr.extend(i)

    wiring = tiny_ocr_root / "hardware" / "wiring.md"
    p, i = extract_wiring_qa(wiring)
    prefs.extend(p)
    instr.extend(i)

    prefs = dedupe_prefs(prefs)
    return prefs, instr


def write_readme(path: Path, *, n_prefs: int, n_instr: int) -> None:
    body = f"""# Arduino / ESP32 coding dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`).

| Field | Value |
|-------|--------|
| Preference rows | {n_prefs} |
| Instruction rows | {n_instr} |

## Regenerate

```bash
python -m training.build_arduino_dataset \\
  --jarvis-root . \\
  --tiny-ocr-root ../tiny-ocr \\
  --out-dir training/datasets/arduino_wand
```
"""
    path.write_text(body, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    here = Path(__file__).resolve().parent
    repo = here.parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jarvis-root", type=Path, default=repo)
    p.add_argument("--tiny-ocr-root", type=Path, default=repo.parent / "tiny-ocr")
    p.add_argument("--out-dir", type=Path, default=here / "datasets" / "arduino_wand")
    p.add_argument("--min-pairs", type=int, default=8)
    args = p.parse_args(argv)

    prefs, instr = build_dataset(args.jarvis_root.resolve(), args.tiny_ocr_root.resolve())
    if len(prefs) < args.min_pairs:
        print(f"refusing to write: got {len(prefs)} pairs, need >= {args.min_pairs}", file=sys.stderr)
        return 3

    out_dir = args.out_dir
    n_p = write_jsonl(out_dir / "preferences.jsonl", (x.to_dict() for x in prefs))
    n_i = write_jsonl(out_dir / "instructions.jsonl", (x.to_dict() for x in instr))
    write_readme(out_dir / "README.md", n_prefs=n_p, n_instr=n_i)
    print(f"wrote {n_p} preferences, {n_i} instructions → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
