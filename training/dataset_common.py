"""Shared types and helpers for Jarvis domain dataset builders."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_LICENSE = "proprietary"


@dataclass
class Instruction:
    prompt: str
    response: str
    kind: str
    source_path: str
    license: str = DEFAULT_LICENSE
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "response": self.response,
            "metadata": {
                "kind": self.kind,
                "source_path": self.source_path,
                "license": self.license,
                "tags": self.tags,
            },
        }


@dataclass
class Preference:
    prompt: str
    chosen: str
    rejected: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "prompt": self.prompt,
            "chosen": self.chosen,
            "rejected": self.rejected,
        }


def make_fence_re(lang: str = "") -> re.Pattern[str]:
    """Match #### headings + fenced code blocks (optional language tag)."""
    if lang:
        tag = re.escape(lang)
        lang_alt = f"(?:{tag})?"
    else:
        lang_alt = r"(?:\w+)?"
    return re.compile(
        rf"####?\s*(?P<label>[^\n]+)\n```(?:{lang_alt})?\n(?P<body>.*?)```",
        re.DOTALL | re.IGNORECASE,
    )


FENCE_RE = make_fence_re("gcode")


def is_correct_label(label: str) -> bool:
    u = label.upper()
    return "CORRECT" in u and "INCORRECT" not in u


def is_incorrect_label(label: str) -> bool:
    return "INCORRECT" in label.upper()


def clean_topic_label(label: str) -> str:
    topic = re.sub(r"^[✅❌🔧\s]*", "", label)
    topic = re.sub(r"(?i)^CORRECT\s*-?\s*", "", topic)
    topic = re.sub(r"(?i)^INCORRECT\s*-?\s*", "", topic)
    return topic.strip()


def dedupe_prefs(prefs: Sequence[Preference]) -> List[Preference]:
    seen: set[tuple[str, str, str]] = set()
    out: List[Preference] = []
    for p in prefs:
        key = (p.prompt, p.chosen, p.rejected)
        if key in seen:
            continue
        if not p.prompt.strip() or not p.chosen.strip() or not p.rejected.strip():
            continue
        if p.chosen.strip() == p.rejected.strip():
            continue
        seen.add(key)
        out.append(p)
    return out


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def extract_code_md_pairs(
    text: str,
    source_path: str,
    *,
    kind: str = "code_style",
    license_: str = DEFAULT_LICENSE,
    tags: Optional[List[str]] = None,
    fence_re: Optional[re.Pattern[str]] = None,
    prompt_fn: Optional[Callable[[str, str], str]] = None,
    synthetic_reject_fn: Optional[Callable[[str], str]] = None,
) -> Tuple[List[Preference], List[Instruction]]:
    """Parse ✅ CORRECT / ❌ INCORRECT fenced blocks from a style guide markdown file."""
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    tags = tags or ["style"]
    pattern = fence_re or FENCE_RE
    blocks: List[Tuple[str, str]] = [
        (m.group("label").strip(), m.group("body").strip()) for m in pattern.finditer(text)
    ]

    i = 0
    while i < len(blocks):
        label, body = blocks[i]
        if is_correct_label(label):
            chosen = body
            rejected: Optional[str] = None
            if i + 1 < len(blocks) and is_incorrect_label(blocks[i + 1][0]):
                rejected = blocks[i + 1][1]
                i += 2
            else:
                if synthetic_reject_fn:
                    rejected = synthetic_reject_fn(chosen)
                else:
                    rejected = chosen  # caller should dedupe; skip if equal
                i += 1
            topic = clean_topic_label(label) or "coding style"
            if prompt_fn:
                prompt = prompt_fn(topic, chosen)
            else:
                prompt = f"Produce the CORRECT form for: {topic}."
            if rejected and rejected.strip() == chosen.strip():
                continue
            prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected or chosen))
            instr.append(
                Instruction(
                    prompt=prompt,
                    response=chosen,
                    kind=kind,
                    source_path=source_path,
                    license=license_,
                    tags=list(tags),
                )
            )
            continue
        if is_incorrect_label(label):
            i += 1
            continue
        i += 1

    return prefs, instr
