"""Unit tests for CAD/CAM dataset builder."""

from __future__ import annotations

import json
from pathlib import Path

from training.build_cad_dataset import (
    build_dataset,
    extract_curated_qa,
    extract_style_md,
    main,
)
from utils.feedback_store import VALID_DOMAINS, infer_domain_from_text


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_cad_in_valid_domains():
    assert "cad" in VALID_DOMAINS


def test_infer_domain_cad():
    assert (
        infer_domain_from_text("Call get_scene_info then create_box in Fusion 360")
        == "cad"
    )
    assert infer_domain_from_text("chipload_mm from Fusion preset") == "cad"


def test_extract_style_md(tmp_path: Path):
    style = tmp_path / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT - one op:
```text
One Fusion mutation per tool call.
```

#### ❌ INCORRECT:
```text
Batch all Fusion mutations.
```
""",
    )
    prefs, instr = extract_style_md(style)
    assert prefs
    assert "One Fusion mutation" in prefs[0].chosen
    assert instr


def test_extract_curated_qa():
    prefs, instr = extract_curated_qa()
    assert len(prefs) >= 30
    assert any("get_scene_info" in p.chosen for p in prefs)


def test_build_dataset_fixture_tree(tmp_path: Path):
    jarvis = tmp_path / "jarvis"
    style = jarvis / "training" / "datasets" / "cad" / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT - ping:
```text
Call ping then get_scene_info.
```

#### ❌ INCORRECT:
```text
Skip ping.
```
""",
    )
    _write(
        jarvis / "docs" / "operations" / "fusion360-mcp.md",
        "Fusion allows one operation per tool call. Prefer parallel_tool_calls false. After TIMEOUT call get_scene_info. Upstream ~90 tools; use create-box prompts.",
    )
    _write(
        jarvis / "docs" / "services" / "tap-collector.md",
        "Shop invariant: post-process.number. Never invents feed. missing_tool_cutting_preset. full CAM sim future. G-code S timing future.",
    )
    _write(
        jarvis / "tap_collector" / "models" / "cutting_force.py",
        'DOC_MAX_MM = 4.5\nWOC_SWEEP_MM = (1.2, 1.35, 1.5)\nMATERIALS = {"6061 aluminum": {}}\nfz_eff = feed_mm_min / (n_teeth * rpm)\n',
    )
    prefs, instr = build_dataset(jarvis)
    assert len(prefs) >= 40
    assert instr


def test_build_dataset_cli(tmp_path: Path):
    jarvis = tmp_path / "jarvis"
    style = jarvis / "training" / "datasets" / "cad" / "STYLE.md"
    _write(
        style,
        """
#### ✅ CORRECT:
```text
domain cad for Fusion MCP feedback.
```

#### ❌ INCORRECT:
```text
Tag Fusion as python_jarvis.
```
""",
    )
    _write(
        jarvis / "docs" / "operations" / "fusion360-mcp.md",
        "one operation per tool call TIMEOUT ~90 tools create-box",
    )
    _write(
        jarvis / "docs" / "services" / "tap-collector.md",
        "Shop invariant post-process.number never invents feed missing_tool_cutting_preset full CAM sim",
    )
    _write(
        jarvis / "tap_collector" / "models" / "cutting_force.py",
        'DOC_MAX_MM = 4.5\nWOC_SWEEP_MM = (1.2,)\n"6061 aluminum"\nfeed_mm_min / (n_teeth * rpm)\n',
    )
    out = jarvis / "training" / "datasets" / "cad"
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
