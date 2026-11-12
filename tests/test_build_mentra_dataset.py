"""Unit tests for MentraOS dataset builder."""

from __future__ import annotations

import json
from pathlib import Path

from training.build_mentra_dataset import (
    build_dataset,
    extract_curated_qa,
    extract_style_md,
    main,
)
from utils.feedback_store import VALID_DOMAINS, infer_domain_from_text


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_mentra_in_valid_domains():
    assert "mentra" in VALID_DOMAINS


def test_infer_domain_mentra():
    assert (
        infer_domain_from_text("Use @mentra/miniapp registerMiniapp for Mentra Live")
        == "mentra"
    )
    assert (
        infer_domain_from_text("BluetoothSdk.scan(DeviceModels.MentraLive)") == "mentra"
    )
    assert infer_domain_from_text("docs.mentraglass.com miniapp session.display") == "mentra"


def test_extract_style_md(tmp_path: Path):
    style = tmp_path / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT - miniapp:
```text
Use @mentra/miniapp registerMiniapp.
```

#### ❌ INCORRECT:
```text
Use legacy @mentra/sdk AppServer for new apps.
```
""",
    )
    prefs, instr = extract_style_md(style)
    assert prefs
    assert "@mentra/miniapp" in prefs[0].chosen
    assert instr


def test_extract_curated_qa():
    prefs, instr = extract_curated_qa()
    assert len(prefs) >= 40
    assert any("@mentra/miniapp" in p.chosen for p in prefs)
    assert any("Expo Go" in p.prompt or "Expo Go" in p.chosen for p in prefs)


def test_build_dataset_fixture_tree(tmp_path: Path):
    jarvis = tmp_path / "jarvis"
    style = jarvis / "training" / "datasets" / "mentra" / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT - ready:
```text
Read session.capabilities inside ready.
```

#### ❌ INCORRECT:
```text
Assume capabilities at handler start.
```
""",
    )
    prefs, instr = build_dataset(jarvis)
    assert len(prefs) >= 40
    assert instr


def test_build_dataset_cli(tmp_path: Path):
    jarvis = tmp_path / "jarvis"
    style = jarvis / "training" / "datasets" / "mentra" / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT:
```text
metadata.domain mentra for Mentra SDK feedback.
```

#### ❌ INCORRECT:
```text
Tag Mentra as python_jarvis.
```
""",
    )
    out = jarvis / "training" / "datasets" / "mentra"
    rc = main(
        [
            "--jarvis-root",
            str(jarvis),
            "--out-dir",
            str(out),
            "--min-pairs",
            "20",
        ]
    )
    assert rc == 0
    rows = [
        json.loads(l)
        for l in (out / "preferences.jsonl").read_text().splitlines()
        if l.strip()
    ]
    assert len(rows) >= 20
    assert (out / "README.md").is_file()
