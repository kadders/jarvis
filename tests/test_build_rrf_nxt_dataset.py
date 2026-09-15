"""Unit tests for NeXT → RRF/nxt dataset builder (fixture trees only)."""
from __future__ import annotations

import json
from pathlib import Path

from training.build_rrf_nxt_dataset import (
    build_dataset,
    extract_code_md_pairs,
    extract_gcode_md,
    extract_generation_style,
    extract_macro_skeletons,
    extract_macros,
    extract_nxt_globals,
    extract_rrf_docs,
    extract_rrf_syntax,
    main,
    strip_braces_antipattern,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_strip_braces_antipattern():
    src = 'echo { "hi" }\nif { exists(param.S) }\nvar x = { 1 }'
    out = strip_braces_antipattern(src)
    assert 'echo { "hi" }' not in out
    assert 'echo "hi"' in out
    assert "if (" in out


def test_extract_code_md_pairs():
    md = """
#### ✅ CORRECT - braced:
```gcode
echo { "ok" }
```

#### ❌ INCORRECT - Missing braces:
```gcode
echo "ok"
```
"""
    prefs, instr = extract_code_md_pairs(md, "docs/CODE.md")
    assert any(p.chosen.strip() == 'echo { "ok" }' for p in prefs)
    assert any(p.rejected.strip() == 'echo "ok"' for p in prefs)
    assert instr and instr[0].kind == "code_style"


def test_extract_gcode_and_macros(tmp_path: Path):
    root = tmp_path / "NeXT"
    _write(
        root / "GCODE.md",
        """# ref

### G6500: Bore Probe

Uses three touches.

**Usage:** `G6500 P|U D<diameter> L<depth>`

**Parameters:**
- `D`: diameter - **REQUIRED**
- `L`: depth - **REQUIRED**

---
""",
    )
    _write(
        root / "macros" / "probing" / "G6500.g",
        '; G6500.g — bore probe\nif { !inputs[state.thisInput].active }\n    M99\n',
    )
    _write(root / "docs" / "CODE.md", "# style\n")
    prefs, instr = extract_gcode_md(
        (root / "GCODE.md").read_text(encoding="utf-8"),
        "GCODE.md",
        root,
    )
    assert prefs and "G6500" in prefs[0].prompt
    assert "Macro excerpt" in prefs[0].chosen

    m_prefs, m_instr = extract_macros(root)
    assert len(m_prefs) == 1
    assert m_instr[0].kind == "macro"
    assert "G6500" in m_prefs[0].prompt


def test_extract_rrf_docs(tmp_path: Path):
    root = tmp_path / "NeXT"
    _write(
        root / "docs" / "RRF_META.txt",
        "vector() must be braced.\nglobal nxtPinStates = { vector(min(limits.gpOutPorts, 32), 0.0) }\n",
    )
    _write(
        root / "docs" / "RRF_LINE_LENGTH.md",
        "# line length\nMaximum **200** characters.\n",
    )
    _write(
        root / "docs" / "RRF_REFERENCE.md",
        "| **Reference RRF** | **3.7.0-beta.1** (3.7.x line) |\n",
    )
    prefs, instr = extract_rrf_docs(root)
    assert len(prefs) >= 3
    assert any("vector()" in p.prompt for p in prefs)
    assert any(i.kind == "rrf_meta" for i in instr)


def test_extract_rrf_syntax_and_globals(tmp_path: Path):
    root = tmp_path / "NeXT"
    _write(
        root / "docs" / "RRF_LINE_LENGTH.md",
        "# line length\nMaximum **200** characters.\n",
    )
    _write(
        root / "docs" / "RRF_3.7_MIGRATION.md",
        "null slots and inputs[state.thisInput].active with M99. Do not add to tpre.\n",
    )
    _write(root / "docs" / "RRF_META.txt", "while { }\n")
    _write(
        root / "docs" / "OM_GLOBAL_SIZE.md",
        "Stay under ~8 KiB. Do not pre-fill nxtToolLife.\n",
    )
    _write(
        root / "macros" / "system" / "nxt-vars.g",
        "; vars\nglobal nxtProbeResults = { vector(5, null) } ; probe table\n"
        "global nxtLoaded = false\n",
    )
    _write(
        root / "ui" / "src" / "utils" / "nxtGlobalsManifest.ts",
        "export const NXT_GLOBAL_MANIFEST = [\n"
        "  { key: 'nxtProbeResults', description: 'Last probe results table' },\n"
        "  { key: 'nxtExtraOnly', description: 'Manifest-only gated key' },\n"
        "]\n",
    )
    syn, syn_i = extract_rrf_syntax(root)
    assert any(i.kind == "rrf_syntax" for i in syn_i)
    assert any("200" in p.chosen for p in syn)

    g_prefs, g_instr = extract_nxt_globals(root)
    assert any(i.kind == "nxt_globals" for i in g_instr)
    assert any("nxtProbeResults" in p.prompt for p in g_prefs)
    assert any("nxtExtraOnly" in p.prompt for p in g_prefs)
    assert any("8 KiB" in p.chosen for p in g_prefs)


def test_extract_generation_and_skeletons(tmp_path: Path):
    root = tmp_path / "NeXT"
    _write(root / "docs" / "CODE.md", "# style\n")
    _write(
        root / "macros" / "probing" / "G6500.g",
        "; G6500 bore\nif { !inputs[state.thisInput].active }\n    M99\n"
        'abort { "G6500: D is required" }\nG27 Z1\n',
    )
    _write(
        root / "macros" / "probing" / "G6512.g",
        "; G6512\nif { !inputs[state.thisInput].active }\n    M99\n",
    )
    _write(
        root / "macros" / "utilities" / "M6520.g",
        "; M6520\nif { !inputs[state.thisInput].active }\n    M99\n",
    )
    _write(
        root / "macros" / "system" / "nxt.g",
        '; nxt boot\nvar nxtRun = { true }\nif { var.nxtRun }\n    M98 P"nxt-vars.g"\n',
    )
    g_prefs, g_instr = extract_generation_style(root)
    assert len(g_prefs) >= 5
    assert all(i.kind == "generation_style" for i in g_instr)

    s_prefs, s_instr = extract_macro_skeletons(root)
    assert len(s_prefs) == 4
    assert all(i.kind == "macro_skeleton" for i in s_instr)
    assert any("inputs[state.thisInput]" in p.chosen for p in s_prefs)


def test_build_dataset_and_cli(tmp_path: Path):
    root = tmp_path / "NeXT"
    _write(
        root / "docs" / "CODE.md",
        """
#### ✅ CORRECT - braced:
```gcode
echo { "a" }
```

#### ❌ INCORRECT - Missing braces:
```gcode
echo "a"
```

### ✅ CORRECT - G-code/M-code Macros
- `G6500.g`

### ❌ INCORRECT - Named Macros Requiring M98
- `probe-workpiece.g`
""",
    )
    _write(
        root / "GCODE.md",
        """### G27: Parking

Moves to a park.

**Usage:** `G27 [Z<level>]`

**Parameters:**
- `Z`: level
""",
    )
    _write(
        root / "macros" / "system" / "nxt.g",
        '; nxt.g boot\nglobal nxtLoaded = { false }\n',
    )
    _write(
        root / "macros" / "system" / "nxt-vars.g",
        "global nxtLoaded = false ; boot flag\n",
    )
    _write(
        root / "macros" / "probing" / "G6500.g",
        "; G6500\nif { !inputs[state.thisInput].active }\n    M99\n",
    )
    _write(
        root / "macros" / "probing" / "G6512.g",
        "; G6512\nif { !inputs[state.thisInput].active }\n    M99\n",
    )
    _write(
        root / "macros" / "utilities" / "M6520.g",
        "; M6520\nif { !inputs[state.thisInput].active }\n    M99\n",
    )
    _write(root / "docs" / "RRF_META.txt", "vector()\n")
    _write(root / "docs" / "RRF_LINE_LENGTH.md", "200 chars\n")
    _write(root / "docs" / "RRF_3.7_MIGRATION.md", "M99 guard\n")
    _write(root / "docs" / "OM_GLOBAL_SIZE.md", "8 KiB budget\n")
    _write(
        root / "docs" / "RRF_REFERENCE.md",
        "| **Reference RRF** | **3.7.0-beta.1** |\n",
    )

    prefs, instr = build_dataset(root)
    kinds = {i.kind for i in instr}
    assert "nxt_globals" in kinds
    assert "rrf_syntax" in kinds or "rrf_meta" in kinds
    assert "generation_style" in kinds
    assert "macro_skeleton" in kinds
    assert len(prefs) >= 8
    assert len(instr) >= 8

    out = tmp_path / "out"
    assert main(["--next-root", str(root), "--out-dir", str(out), "--min-pairs", "8"]) == 0
    pref_path = out / "preferences.jsonl"
    lines = pref_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 8
    rec = json.loads(lines[0])
    assert set(rec) == {"prompt", "chosen", "rejected"}
    assert (out / "instructions.jsonl").is_file()
    assert (out / "README.md").is_file()
