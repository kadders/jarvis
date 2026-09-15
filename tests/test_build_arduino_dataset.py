"""Unit tests for Arduino dataset builder."""
from __future__ import annotations

import json
from pathlib import Path

from training.build_arduino_dataset import build_dataset, extract_style_md, main


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_extract_style_md(tmp_path: Path):
    style = tmp_path / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT - non-blocking:
```cpp
if (millis() - lastPollMs >= 500) { poll(); }
```

#### ❌ INCORRECT:
```cpp
delay(500); poll();
```
""",
    )
    prefs, instr = extract_style_md(style)
    assert prefs
    assert "millis" in prefs[0].chosen


def test_build_dataset_fixture_tree(tmp_path: Path):
    jarvis = tmp_path / "jarvis"
    style = jarvis / "training" / "datasets" / "arduino_wand" / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT:
```cpp
http.end();
```

#### ❌ INCORRECT:
```cpp
// missing http.end()
```
""",
    )
    _write(
        jarvis / "wand" / "edge" / "esp32" / "src" / "main.cpp",
        "void loop() {\n  HTTPClient http;\n  http.begin(url);\n  http.GET();\n  http.end();\n}\n",
    )
    _write(jarvis / "wand" / "edge" / "esp32" / "platformio.ini", "[env]\nframework = arduino\n")
    prefs, instr = build_dataset(jarvis, tmp_path / "ocr")
    assert len(prefs) >= 2


def test_build_dataset_cli(tmp_path: Path):
    jarvis = tmp_path / "jarvis"
    style = jarvis / "training" / "datasets" / "arduino_wand" / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT:
```cpp
WiFi.mode(WIFI_STA);
```

#### ❌ INCORRECT:
```cpp
WiFi.mode(WIFI_AP);
```
""",
    )
    out = jarvis / "training" / "datasets" / "arduino_wand"
    rc = main(
        [
            "--jarvis-root",
            str(jarvis),
            "--tiny-ocr-root",
            str(tmp_path / "ocr"),
            "--out-dir",
            str(out),
            "--min-pairs",
            "1",
        ]
    )
    assert rc == 0
    rows = [json.loads(l) for l in (out / "preferences.jsonl").read_text().splitlines() if l.strip()]
    assert rows
