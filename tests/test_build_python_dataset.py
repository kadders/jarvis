"""Unit tests for Python dataset builder."""
from __future__ import annotations

import json
from pathlib import Path

from training.build_python_dataset import build_dataset, extract_style_md, main


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_extract_style_md(tmp_path: Path):
    style = tmp_path / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT:
```python
x = 1
```

#### ❌ INCORRECT:
```python
x=1
```
""",
    )
    prefs, instr = extract_style_md(style)
    assert prefs
    assert instr[0].kind == "python_style"


def test_build_dataset_fixture_tree(tmp_path: Path):
    jarvis = tmp_path / "jarvis"
    style = jarvis / "training" / "datasets" / "python_jarvis" / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT - env:
```python
import os

X = os.environ.get("FOO", "bar")
```

#### ❌ INCORRECT:
```python
X = "/hardcoded/path"
```
""",
    )
    _write(
        jarvis / "api" / "routers" / "health.py",
        'def health():\n    return {"status": "ok"}\n',
    )
    prefs, instr = build_dataset(jarvis, tmp_path / "tap", tmp_path / "ocr")
    assert len(prefs) >= 3
    assert len(instr) >= 3


def test_build_dataset_cli(tmp_path: Path):
    jarvis = tmp_path / "jarvis"
    style = jarvis / "training" / "datasets" / "python_jarvis" / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT:
```python
def ok() -> int:
    return 1
```

#### ❌ INCORRECT:
```python
def ok():
    return 1
```
""",
    )
    out = jarvis / "training" / "datasets" / "python_jarvis"
    rc = main(
        [
            "--jarvis-root",
            str(jarvis),
            "--tap-testing-root",
            str(tmp_path / "tap"),
            "--tiny-ocr-root",
            str(tmp_path / "ocr"),
            "--out-dir",
            str(out),
            "--min-pairs",
            "2",
        ]
    )
    assert rc == 0
    rows = [json.loads(l) for l in (out / "preferences.jsonl").read_text().splitlines() if l.strip()]
    assert len(rows) >= 2
