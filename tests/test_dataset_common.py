"""Tests for training.dataset_common."""
from __future__ import annotations

from training.dataset_common import (
    Preference,
    dedupe_prefs,
    extract_code_md_pairs,
    make_fence_re,
)


def test_dedupe_prefs():
    prefs = [
        Preference(prompt="a", chosen="b", rejected="c"),
        Preference(prompt="a", chosen="b", rejected="c"),
        Preference(prompt="x", chosen="y", rejected="z"),
    ]
    out = dedupe_prefs(prefs)
    assert len(out) == 2


def test_extract_code_md_pairs_python():
    md = """
#### ✅ CORRECT - Black quotes:
```python
msg = "hello"
```

#### ❌ INCORRECT - Single quotes:
```python
msg = 'hello'
```
"""
    fence = make_fence_re("python")
    prefs, instr = extract_code_md_pairs(
        md,
        "STYLE.md",
        kind="python_style",
        fence_re=fence,
    )
    assert len(prefs) == 1
    assert 'msg = "hello"' in prefs[0].chosen
    assert instr[0].kind == "python_style"
