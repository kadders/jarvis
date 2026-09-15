#!/usr/bin/env python3
"""
Build an RRF / nxt coding preference dataset from the NeXT (MOS-nxt) repo.

Sources (NeXT only):
  - docs/CODE.md          correct/incorrect style + generation_style rules
  - GCODE.md              custom G/M API Q&A
  - macros/{probing,utilities,canned,system}/*.g
  - docs/RRF_META.txt, RRF_LINE_LENGTH.md, RRF_REFERENCE.md, RRF_3.7_MIGRATION.md
  - docs/OM_GLOBAL_SIZE.md, macros/system/nxt-vars.g
  - ui/src/utils/nxtGlobalsManifest.ts (optional descriptions)
  - Representative macro skeletons (G6500, G6512, M6520, nxt.g)

Writes:
  preferences.jsonl   {prompt, chosen, rejected} for training.train_dpo
  instructions.jsonl  prompt/response + provenance metadata
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from training.dataset_common import (
    FENCE_RE,
    Instruction,
    Preference,
    dedupe_prefs,
    is_correct_label as _is_correct_label,
    is_incorrect_label as _is_incorrect_label,
    write_jsonl,
)

LICENSE = "GPL-3.0"

MACRO_SUBDIRS = ("probing", "utilities", "canned", "system")
MAX_MACRO_CHARS = 3500
MAX_SECTION_CHARS = 2500
MAX_SKELETON_CHARS = 2200
SKELETON_MACROS = (
    "macros/probing/G6500.g",
    "macros/probing/G6512.g",
    "macros/utilities/M6520.g",
    "macros/system/nxt.g",
)
GLOBAL_DECL_RE = re.compile(
    r"^global\s+(?P<name>nxt\w+)\s*=\s*(?P<rhs>.+?)(?:\s*;\s*(?P<comment>.*))?$",
    re.IGNORECASE,
)
MANIFEST_ENTRY_RE = re.compile(
    r"\{\s*key:\s*'(?P<key>nxt[^']+)'\s*,\s*description:\s*'(?P<desc>(?:\\'|[^'])*)'\s*\}",
)

SECTION_RE = re.compile(
    r"^###\s+(?P<title>.+?)\s*$\n(?P<body>.*?)(?=^###\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)
USAGE_RE = re.compile(r"\*\*Usage:\*\*\s*`([^`]+)`")
CODE_TOKEN_RE = re.compile(r"^([GM]\d+(?:\.\d+)?)", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def strip_braces_antipattern(code: str) -> str:
    """Cheap synthetic reject: peel outer `{…}` wrappers from meta expressions."""
    out: List[str] = []
    for line in code.splitlines():
        s = line
        # echo { "…" } / abort { "…" }
        s = re.sub(
            r"^(?P<indent>\s*)(?P<cmd>echo|abort)\s+\{\s*(?P<body>.+?)\s*\}\s*$",
            r"\g<indent>\g<cmd> \g<body>",
            s,
        )
        # var x = { expr } / set … = { expr }
        s = re.sub(
            r"^(?P<head>\s*(?:var\s+\w+|set\s+[\w.\[\]]+)\s*=)\s*\{\s*(?P<body>.+?)\s*\}\s*$",
            r"\g<head> \g<body>",
            s,
        )
        # if { cond }
        s = re.sub(
            r"^(?P<head>\s*(?:if|while|elif)\s+)\{\s*(?P<body>.+?)\s*\}\s*$",
            r"\g<head>(\g<body>)",
            s,
        )
        out.append(s)
    return "\n".join(out)


def wrong_global_prefix(code: str) -> str:
    return re.sub(r"\bnxt([A-Z]\w*)", r"mos\1", code)


def string_build_antipattern() -> str:
    return (
        'var x = {1}\n'
        'var y = {2}\n'
        'var gcode = {"G0 X" ^ var.x ^ " Y" ^ var.y}\n'
        "{gcode}"
    )


def long_line_antipattern() -> str:
    return (
        "if { "
        + " || ".join(f'fileexists("0:/sys/fake-path-{i}.g")' for i in range(12))
        + " }"
    )


def named_macro_antipattern(stem: str) -> str:
    return f'M98 P"{stem}.g"\n; named macro — prefer a G/M code file instead'


def synthetic_reject(chosen: str, *, hint: str = "") -> str:
    """Pick a plausible wrong answer when no labeled incorrect example exists."""
    if "vector(" in chosen and "{ vector" in chosen:
        return chosen.replace("{ vector(", "vector(").replace(") }", ")")
    if "echo {" in chosen or "abort {" in chosen or "if {" in chosen:
        stripped = strip_braces_antipattern(chosen)
        if stripped.strip() and stripped != chosen:
            return stripped
    if "global.nxt" in chosen or "nxt" in chosen.lower():
        flipped = wrong_global_prefix(chosen)
        if flipped != chosen:
            return flipped
    if hint == "gcode_api":
        return (
            "Call a named helper with M98 instead of a G/M macro, "
            "and omit curly braces around meta expressions.\n"
            + named_macro_antipattern("probe-workpiece")
        )
    if hint == "rrf_meta" or hint == "rrf_syntax":
        return (
            "while (iterations < #move.axes)  ; parentheses start a CNC comment\n"
            "global nxtProbeResults = vector(#move.axes + 1, 0.0)  ; missing { }"
        )
    if hint == "line_length":
        return long_line_antipattern()
    if hint == "nxt_globals":
        return (
            "global probeResults = vector(limits.tools, 0.0)\n"
            "global mosProbeResults = { vector(limits.tools, 0.0) }\n"
            "; fat boot vectors and wrong prefixes blow the ~8 KiB global OM budget"
        )
    if hint == "macro_skeleton":
        return (
            '; probe-workpiece.g — named helper\n'
            'M98 P"probe-workpiece.g"\n'
            + string_build_antipattern()
        )
    return string_build_antipattern()


def extract_generation_style(
    next_root: Path,
) -> Tuple[List[Preference], List[Instruction]]:
    """CODE.md rules that are not already covered by fenced CORRECT/INCORRECT pairs."""
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    source = "docs/CODE.md"
    pairs = [
        (
            "Write nxt meta G-code that validates multiple axis parameters (X/Y/Z/A) "
            "and selects exactly one. You cannot iterate `param` directly.",
            (
                "var axisParams = { param.X, param.Y, param.Z, param.A }\n"
                "var selectedAxis = -1\n"
                "\n"
                "while { iterations < #var.axisParams }\n"
                "    if { var.axisParams[iterations] != null }\n"
                "        if { var.selectedAxis != -1 }\n"
                "            abort { \"Only one axis parameter allowed\" }\n"
                "        set var.selectedAxis = { iterations }"
            ),
            (
                "while { iterations < #param }\n"
                "    if { param[iterations] != null }\n"
                "        set var.selectedAxis = { iterations }"
            ),
            ["parameters", "iteration"],
        ),
        (
            "Write an nxt loop that aborts if any configured axis is not homed. "
            "Use the object model, not hard-coded axis indexes.",
            (
                "while { iterations < #move.axes }\n"
                "    if { !move.axes[iterations].homed }\n"
                "        abort { \"Axis \" ^ move.axes[iterations].letter "
                '^ " is not homed. Please home all axes before continuing." }'
            ),
            (
                "if { !move.axes[0].homed || !move.axes[1].homed || !move.axes[2].homed }\n"
                "    abort { \"Axes not homed\" }"
            ),
            ["object_model", "homing"],
        ),
        (
            "Write an nxt loop over tools that safely skips null sparse tool slots.",
            (
                "while { iterations < #tools }\n"
                "    if { tools[iterations] != null && tools[iterations].offsets[0] > 10 }\n"
                '        echo { "Tool " ^ iterations ^ " has X offset > 10mm" }'
            ),
            (
                "while { iterations < #tools }\n"
                "    if { tools[iterations].offsets[0] > 10 }\n"
                '        echo "Tool " ^ iterations ^ " bad"'
            ),
            ["object_model", "sparse"],
        ),
        (
            "Show how to initialize an nxtProbeResults row on RRF 3.7 before taking "
            "its length with `#` when the slot may be null.",
            (
                "if { global.nxtProbeResults[var.pSlot] == null "
                "|| #global.nxtProbeResults[var.pSlot] < 3 }\n"
                "    set global.nxtProbeResults[var.pSlot] = "
                "{ vector(#move.axes + 1, 0.0) }"
            ),
            (
                "set global.nxtProbeResults[var.pSlot] = "
                "vector(#global.nxtProbeResults[var.pSlot], 0.0)"
            ),
            ["rrf37", "probe_results"],
        ),
        (
            "In nxt probing macros, should workpiece dimensions (W/H/L/D) be required "
            "parameters or silent defaults? State the rule and why.",
            (
                "Workpiece dimensions must be **required** parameters (e.g. W/H/L/D). "
                "Do not invent silent defaults for stock size — wrong defaults crash tools "
                "into fixtures. Optional knobs like overtravel O, feed F, or retries R may "
                "have defaults. Probe result slot P (or workplace U) is mandatory."
            ),
            (
                "Default D=50 and L=10 when omitted so CAM posts stay short; "
                "P can be optional."
            ),
            ["probing", "parameters"],
        ),
        (
            "Emit a short nxt abort for a missing required parameter P on macro G6512.",
            'abort { "G6512: P is required" }',
            'abort "G6512: P is required"',
            ["abort", "validation"],
        ),
        (
            "How should nxt wrap stock M3 spindle control to add safety without replacing M3?",
            (
                "Create a wrapper macro `M3.9.g` with decimal extension. Put custom logic "
                "there, then call base `M3`. Post-processors emit `M3.9`."
            ),
            "Overwrite M3.g entirely and require CAM to call a named spindle-on.g via M98.",
            ["wrappers", "spindle"],
        ),
        (
            "When should an nxt machine be parked with G27 before probing or tool work?",
            (
                "Park to a safe Z (typically `G27 Z1`) before tool changes, spindle "
                "start/stop, probing, and tool-length measurements so subsequent moves "
                "clear the workpiece."
            ),
            "Never park; rapid straight into the probe cycle from the current Z.",
            ["parking", "G27"],
        ),
    ]
    for prompt, chosen, rejected, tags in pairs:
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="generation_style",
                source_path=source,
                tags=["CODE.md", *tags],
            )
        )
    return prefs, instr


def extract_rrf_syntax(
    next_root: Path,
) -> Tuple[List[Preference], List[Instruction]]:
    """Additional RRF 3.7 formatting / meta syntax preferences (200-char policy)."""
    prefs: List[Preference] = []
    instr: List[Instruction] = []

    line_path = next_root / "docs" / "RRF_LINE_LENGTH.md"
    if line_path.is_file():
        rel = str(line_path.relative_to(next_root))
        good_if = (
            "var nxtMosImportForced = { fileexists(\"0:/sys/nxt-mos-import.requested\") }\n"
            "var nxtNeedsUserVars = { !fileexists(\"0:/sys/nxt-user-vars.g\") }\n"
            "var nxtMosOnSd = { fileexists(\"0:/sys/mos-vars.g\") "
            "|| fileexists(\"0:/sys/mos-user-vars.g\") }\n"
            "var nxtRunMosImport = { var.nxtMosImportForced "
            "|| (var.nxtNeedsUserVars && var.nxtMosOnSd) }\n"
            "if { var.nxtRunMosImport }\n"
            '    M98 P"nxt-mos-import.g"'
        )
        prompt = (
            "Refactor a long compound nxt boot `if` that exceeds the 200-character "
            "line policy into `var` booleans and a short final `if`."
        )
        prefs.append(
            Preference(prompt=prompt, chosen=good_if, rejected=long_line_antipattern())
        )
        instr.append(
            Instruction(
                prompt=prompt,
                response=good_if,
                kind="rrf_syntax",
                source_path=rel,
                tags=["RRF_LINE_LENGTH", "refactor"],
            )
        )

        prompt_m291 = (
            "Show the nxt-safe way to issue a long M291 caution dialog without "
            "exceeding 200 characters on one line."
        )
        chosen_m291 = (
            'var nxtM81Msg = {"<b>CAUTION</b>: Machine Power is <b>on</b>. Deactivate?"}\n'
            'M291 P{var.nxtM81Msg} R"nxt: Safety Net" S4 K{"Deactivate", "Cancel"} F1'
        )
        rejected_m291 = (
            'M291 P{"<b>CAUTION</b>: Machine Power is <b>on</b>. Deactivate? '
            + ("Stops ALL movement and spindle. " * 8)
            + '"} R"nxt: Safety Net" S4'
        )
        prefs.append(
            Preference(prompt=prompt_m291, chosen=chosen_m291, rejected=rejected_m291)
        )
        instr.append(
            Instruction(
                prompt=prompt_m291,
                response=chosen_m291,
                kind="rrf_syntax",
                source_path=rel,
                tags=["RRF_LINE_LENGTH", "M291"],
            )
        )

        prompt_echo = (
            "Show how to build a long nxt echo/abort message under the 200-char "
            "line limit."
        )
        chosen_echo = (
            'var msg = { "G6500: Starting bore probe" }\n'
            "set var.msg = { var.msg ^ \" diameter=\" ^ var.boreDiameter }\n"
            "echo { var.msg }"
        )
        rejected_echo = (
            'echo { "G6500: Starting bore probe diameter=" ^ var.boreDiameter ^ '
            '" overtravel=" ^ var.overtravel ^ " depth=" ^ var.probeDepth ^ '
            '" retries=" ^ var.retries ^ " skew=" ^ var.skewLimit ^ '
            '" and many more fields that make this one physical line too long" }'
        )
        prefs.append(
            Preference(prompt=prompt_echo, chosen=chosen_echo, rejected=rejected_echo)
        )
        instr.append(
            Instruction(
                prompt=prompt_echo,
                response=chosen_echo,
                kind="rrf_syntax",
                source_path=rel,
                tags=["RRF_LINE_LENGTH", "echo"],
            )
        )

    mig_path = next_root / "docs" / "RRF_3.7_MIGRATION.md"
    if mig_path.is_file():
        rel = str(mig_path.relative_to(next_root))
        prompt = (
            "On RRF 3.7, how should nxt macros treat null object-model values and "
            "`#` length on possibly-null probe result rows?"
        )
        chosen = (
            "RRF 3.7 may return `null`/`false` instead of errors. Guard with "
            "`exists(global.*)` and `!= null`. Before `#` on a probe result row, "
            "initialize when null:\n"
            "  if { global.nxtProbeResults[var.pSlot] == null "
            "|| #global.nxtProbeResults[var.pSlot] < 3 }\n"
            "      set global.nxtProbeResults[var.pSlot] = "
            "{ vector(#move.axes + 1, 0.0) }\n"
            "Do not rely on expression errors as control flow."
        )
        rejected = (
            "Assume `#global.nxtProbeResults[var.pSlot]` always works; "
            "use thrown meta errors to skip uninitialized rows."
        )
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="rrf_syntax",
                source_path=rel,
                tags=["RRF_3.7_MIGRATION", "null"],
            )
        )

        prompt2 = (
            "Emit the RRF 3.7 primary motion-system early-return guard for a "
            "user-callable nxt G/M macro. Name the tool-change files that must omit it."
        )
        chosen2 = (
            "if { !inputs[state.thisInput].active }\n"
            "    M99\n"
            "; Do NOT add this guard to tpre.g, tfree.g, or tpost.g."
        )
        rejected2 = (
            "if { !inputs[state.thisInput].active }\n"
            "    M99\n"
            "; Also paste this into tpre.g / tfree.g / tpost.g for safety."
        )
        prefs.append(Preference(prompt=prompt2, chosen=chosen2, rejected=rejected2))
        instr.append(
            Instruction(
                prompt=prompt2,
                response=chosen2,
                kind="rrf_syntax",
                source_path=rel,
                tags=["RRF_3.7_MIGRATION", "M99"],
            )
        )

    meta_path = next_root / "docs" / "RRF_META.txt"
    if meta_path.is_file():
        rel = str(meta_path.relative_to(next_root))
        prompt = (
            "In nxt CNC-mode macros, show correct vs incorrect `while` forms and "
            "state the nxt line-length policy number (not the raw RRF ~255 cliff)."
        )
        chosen = (
            "Correct: `while { iterations < #move.axes }`\n"
            "Incorrect: `while (iterations < #move.axes)` — `(…)` starts a CNC comment.\n"
            "nxt CI policy: keep every non-comment `.g` line ≤ **200** characters."
        )
        rejected = (
            "while (iterations < #move.axes)\n"
            "; lines may be up to 255 characters under nxt policy"
        )
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="rrf_syntax",
                source_path=rel,
                tags=["RRF_META", "cnc_mode", "200"],
            )
        )

    return prefs, instr


def _load_globals_manifest(next_root: Path) -> Dict[str, str]:
    path = next_root / "ui" / "src" / "utils" / "nxtGlobalsManifest.ts"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    out: Dict[str, str] = {}
    for m in MANIFEST_ENTRY_RE.finditer(text):
        out[m.group("key")] = m.group("desc").replace("\\'", "'")
    return out


def extract_nxt_globals(
    next_root: Path,
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    manifest = _load_globals_manifest(next_root)

    om_path = next_root / "docs" / "OM_GLOBAL_SIZE.md"
    if om_path.is_file():
        rel = str(om_path.relative_to(next_root))
        budget_pairs = [
            (
                "What approximate Object Model `global` size budget must nxt stay under "
                "on SBC/DSF, and how do you check it?",
                (
                    "Stay comfortably under ~8 KiB for the whole `global` key (8192 is "
                    "the cliff). Prefer null session slots and lean `nxt-vars.g`. Verify "
                    "with `node dist/check-om-global-budget.mjs`."
                ),
                "Allocate full `vector(limits.tools, 0.0)` caches at boot; size is unlimited.",
                ["budget"],
            ),
            (
                "Should nxt declare `nxtToolLife = { vector(limits.tools, 0.0) }` in "
                "nxt-vars.g at boot? Explain the correct approach.",
                (
                    "No. Leave `global nxtToolLife = null` and allocate on first use. "
                    "A 50× float vector at boot can push Custom machines over the ~8 KiB "
                    "global OM cap."
                ),
                "global nxtToolLife = { vector(limits.tools, 0.0) }",
                ["nxtToolLife"],
            ),
            (
                "Summarize the nxt global boot overlay order from OM_GLOBAL_SIZE.md "
                "(nxt-vars through user-overrides).",
                (
                    "1) nxt-vars.g lean defaults\n"
                    "2) nxt-custom-globals.g when requested/overlays exist\n"
                    "3) optional MOS import\n"
                    "4) nxt-tooltable.g (mosTT→nxtTT)\n"
                    "5) nxt-probe-wcs.g / align as needed\n"
                    "6) nxt-user-vars.g (set-only)\n"
                    "7) board pack / tools / plugin-init\n"
                    "8) nxt-user-overrides.g last → nxtLoaded"
                ),
                "Load nxt-user-overrides.g first, then overwrite with nxt-vars.g forever.",
                ["boot_order"],
            ),
            (
                "How should tool-length cache be stored in nxt globals to protect OM size?",
                (
                    "Use scalars `nxtToolCacheIdx` / `nxtToolCacheZ`, not "
                    "`vector(limits.tools)`."
                ),
                "global nxtToolCache = { vector(limits.tools, null) }",
                ["tool_cache"],
            ),
        ]
        for prompt, chosen, rejected, tags in budget_pairs:
            prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
            instr.append(
                Instruction(
                    prompt=prompt,
                    response=chosen,
                    kind="nxt_globals",
                    source_path=rel,
                    tags=["OM_GLOBAL_SIZE", *tags],
                )
            )

    vars_path = next_root / "macros" / "system" / "nxt-vars.g"
    if vars_path.is_file():
        rel = str(vars_path.relative_to(next_root))
        for line in vars_path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s.startswith("global "):
                continue
            m = GLOBAL_DECL_RE.match(s)
            if not m:
                continue
            name = m.group("name")
            rhs = m.group("rhs").strip()
            comment = (m.group("comment") or "").strip()
            desc = manifest.get(name) or comment or "nxt system global"
            prompt = (
                f"Document nxt global `{name}` for RRF macro authors: purpose, "
                f"default/RHS form, and safe update pattern (declare vs set)."
            )
            chosen = (
                f"`global.{name}` — {desc}\n"
                f"Default in nxt-vars.g:\n  global {name} = {rhs}\n"
                "Naming: `nxt` + camelCase. After first declare, later files should "
                "`set global."
                f"{name} = {{ … }}` (user-vars are set-only). Keep boot values lean; "
                "do not expand fat vectors until needed."
            )
            rejected = (
                f"global {name.replace('nxt', 'mos', 1)} = {rhs}\n"
                f"; or persist set global.{name} = null in nxt-user-vars.g"
            )
            if rejected.strip() == chosen.strip():
                rejected = synthetic_reject(chosen, hint="nxt_globals")
            prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
            instr.append(
                Instruction(
                    prompt=prompt,
                    response=chosen,
                    kind="nxt_globals",
                    source_path=rel,
                    tags=["nxt-vars.g", name],
                )
            )

        # Manifest-only keys (optional / gated) — short lifecycle notes
        declared = {i.tags[-1] for i in instr if i.kind == "nxt_globals" and i.tags}
        for key, desc in sorted(manifest.items()):
            if key in declared:
                continue
            prompt = (
                f"When is nxt global `{key}` expected to exist, and what is it for?"
            )
            chosen = (
                f"`global.{key}` — {desc}\n"
                "It may be declare-on-use / plugin-gated rather than always-on in "
                "nxt-vars.g. Check `exists(global."
                f"{key})` before read; prefer null until needed for OM budget."
            )
            rejected = (
                f"Always add `global {key} = {{ vector(limits.tools, 0.0) }}` to "
                "nxt-vars.g at boot without feature gates."
            )
            prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
            instr.append(
                Instruction(
                    prompt=prompt,
                    response=chosen,
                    kind="nxt_globals",
                    source_path="ui/src/utils/nxtGlobalsManifest.ts",
                    tags=["manifest", key],
                )
            )

    return prefs, instr


def _macro_skeleton_excerpt(text: str) -> str:
    """Header + early guards/validation for generation gold."""
    lines = text.splitlines()
    out: List[str] = []
    saw_code = False
    for line in lines:
        out.append(line)
        s = line.strip()
        if s and not s.startswith(";"):
            saw_code = True
        if saw_code and len("\n".join(out)) >= MAX_SKELETON_CHARS:
            break
        if saw_code and len(out) > 80:
            break
    body = "\n".join(out).strip()
    if len(body) > MAX_SKELETON_CHARS:
        body = body[:MAX_SKELETON_CHARS] + "\n; … truncated …"
    return body


def extract_macro_skeletons(
    next_root: Path,
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    prompts = {
        "macros/probing/G6500.g": (
            "Write the opening of an nxt RRF bore-probe macro G6500.g: header, "
            "RRF 3.7 motion-system guard, feature/tool checks, required P|U and D/L "
            "validation with abort, then park with G27. Use braced meta expressions "
            "and nxt* globals."
        ),
        "macros/probing/G6512.g": (
            "Write the opening of nxt protected-move / probe helper G6512.g including "
            "the RRF 3.7 inputs[state.thisInput] guard, parameter validation pattern, "
            "and abort messages prefixed with G6512."
        ),
        "macros/utilities/M6520.g": (
            "Write the opening of nxt M6520.g (apply probe result / WCS utility): "
            "header comments, motion-system M99 guard, and early validation using "
            "global.nxtProbeResults."
        ),
        "macros/system/nxt.g": (
            "Show how nxt.g should structure long boot conditions under the 200-char "
            "line policy (var booleans + short if) and keep CNC-safe braced meta "
            "expressions. Prefer the real nxt.g style."
        ),
    }
    for rel, prompt in prompts.items():
        path = next_root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        chosen = _macro_skeleton_excerpt(text)
        rejected = strip_braces_antipattern(chosen)
        if rejected == chosen:
            rejected = synthetic_reject(chosen, hint="macro_skeleton")
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="macro_skeleton",
                source_path=rel,
                tags=["skeleton", Path(rel).stem],
            )
        )
    return prefs, instr


def extract_code_md_pairs(text: str, source_path: str) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    blocks: List[Tuple[str, str]] = [
        (m.group("label").strip(), m.group("body").strip()) for m in FENCE_RE.finditer(text)
    ]

    i = 0
    while i < len(blocks):
        label, body = blocks[i]
        if _is_correct_label(label):
            chosen = body
            rejected: Optional[str] = None
            if i + 1 < len(blocks) and _is_incorrect_label(blocks[i + 1][0]):
                rejected = blocks[i + 1][1]
                i += 2
            else:
                rejected = synthetic_reject(chosen, hint="style")
                i += 1
            topic = re.sub(r"^[✅❌🔧\s]*", "", label)
            topic = re.sub(r"(?i)^CORRECT\s*-?\s*", "", topic).strip() or "RRF meta G-code style"
            prompt = (
                "You are writing nxt (MOS-nxt) RepRapFirmware meta G-code. "
                f"Produce the CORRECT form for: {topic}. "
                "Follow nxt coding style (brace all expressions, nxt* globals)."
            )
            prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
            instr.append(
                Instruction(
                    prompt=prompt,
                    response=chosen,
                    kind="code_style",
                    source_path=source_path,
                    tags=["CODE.md", "style"],
                )
            )
            continue
        if _is_incorrect_label(label):
            # Orphan incorrect block — skip (paired above when possible)
            i += 1
            continue
        i += 1

    # Section 8 list-style naming examples (no fences)
    if "CORRECT - G-code/M-code Macros" in text and "INCORRECT - Named Macros" in text:
        prompt = (
            "For nxt user-callable macros, should probing be exposed as a G/M-code "
            "file (e.g. G6500.g) or as a named file invoked via M98? "
            "Give the preferred approach with examples."
        )
        chosen = (
            "Prefer G-code/M-code macros called directly:\n"
            "- G37.g, G6500.g, M4000.g, M6515.g\n"
            "Do NOT create named macros that require M98 for user/post-processor calls "
            "(e.g. measure-tool-length.g). Named files are OK only for internal helpers "
            "invoked via M98 from other macros (e.g. nxt-vars.g, nxt-canned-spindle.g)."
        )
        rejected = (
            "Create named macros like measure-tool-length.g and probe-workpiece.g "
            "and call them with M98 P\"probe-workpiece.g\" from CAM and DWC."
        )
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="code_style",
                source_path=source_path,
                tags=["CODE.md", "naming"],
            )
        )

    # Global naming rule
    prompt = (
        "In nxt RepRapFirmware macros, how must global variables be named? "
        "Give the rule and one example."
    )
    chosen = (
        "All globals must be prefixed with `nxt` and use camelCase "
        "(e.g. `global.nxtProbeResults`, `global.nxtCoolantFloodID`). "
        "Avoid short abbreviations like `nxtCFID`."
    )
    rejected = (
        "Use short unprefixed names or mos* prefixes freely, e.g. `global.probeResults` "
        "or `global.mosProbeResults`."
    )
    prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
    instr.append(
        Instruction(
            prompt=prompt,
            response=chosen,
            kind="code_style",
            source_path=source_path,
            tags=["CODE.md", "globals"],
        )
    )
    return prefs, instr


def _code_token_from_title(title: str) -> Optional[str]:
    m = CODE_TOKEN_RE.match(title.strip())
    if m:
        return m.group(1).upper().replace("G", "G").replace("M", "M")
    # M3.9, M4.9 style titles
    m2 = re.match(r"^([GM]\d+(?:\.\d+)?)", title.strip(), re.I)
    return m2.group(1) if m2 else None


def _find_macro_file(next_root: Path, code: str) -> Optional[Path]:
    """Locate macros/**/{code}.g (try exact then stem)."""
    name = f"{code}.g"
    # Also try without case issues
    for sub in MACRO_SUBDIRS:
        candidate = next_root / "macros" / sub / name
        if candidate.is_file():
            return candidate
    matches = list((next_root / "macros").rglob(name))
    return matches[0] if matches else None


def extract_gcode_md(
    text: str, source_path: str, next_root: Path
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    for m in SECTION_RE.finditer(text):
        title = m.group("title").strip()
        body = m.group("body").strip()
        if title.lower().startswith("table of contents"):
            continue
        if len(body) < 40:
            continue
        code = _code_token_from_title(title)
        # Skip non-API narrative sections without a G/M token unless useful
        if not code and ":" not in title:
            continue
        usage = USAGE_RE.search(body)
        usage_s = usage.group(1) if usage else ""
        # Truncate body for chosen
        chosen_parts = [f"# {title}", body[:MAX_SECTION_CHARS]]
        if code:
            macro = _find_macro_file(next_root, code)
            if macro is None and "." in code:
                # M3.9 → try M3.9.g
                macro = _find_macro_file(next_root, code)
            if macro is not None:
                excerpt = macro.read_text(encoding="utf-8", errors="replace")[:1200]
                chosen_parts.append(
                    f"\n---\nMacro excerpt (`{macro.relative_to(next_root)}`):\n```gcode\n{excerpt}\n```"
                )
        chosen = "\n".join(chosen_parts).strip()
        label = code or title.split(":")[0].strip()
        prompt = (
            f"Explain and document the nxt custom code `{label}` for RepRapFirmware. "
            "Include usage, required parameters, and important behavior notes."
        )
        if usage_s:
            prompt += f" Usage hint: `{usage_s}`."
        rejected = synthetic_reject(chosen[:800], hint="gcode_api")
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="gcode_api",
                source_path=source_path,
                tags=["GCODE.md", label],
            )
        )
    return prefs, instr


def _macro_header(text: str) -> str:
    lines: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(";"):
            lines.append(s)
            if len(lines) >= 12:
                break
        elif s and not s.startswith(";"):
            break
    return "\n".join(lines)


def extract_macros(
    next_root: Path,
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    macros_root = next_root / "macros"
    for sub in MACRO_SUBDIRS:
        directory = macros_root / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.g")):
            # Skip board/machine packs under system if any slipped in
            rel = path.relative_to(next_root).as_posix()
            if "nxt-config" in rel:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if not text.strip():
                continue
            header = _macro_header(text)
            body = text.strip()
            if len(body) > MAX_MACRO_CHARS:
                body = body[:MAX_MACRO_CHARS] + "\n; … truncated for dataset …"
            stem = path.stem
            prompt = (
                f"Implement or explain the nxt RRF macro `{stem}` "
                f"(path `{rel}`). Preserve meta G-code bracing and nxt* globals."
            )
            if header:
                prompt += f"\nHeader comments:\n{header}"
            chosen = body
            rejected = strip_braces_antipattern(body)
            if rejected == chosen:
                rejected = wrong_global_prefix(body)
            if rejected == chosen:
                rejected = named_macro_antipattern(stem.lower().replace(".", "-"))
            prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
            instr.append(
                Instruction(
                    prompt=prompt,
                    response=chosen,
                    kind="macro",
                    source_path=rel,
                    tags=["macro", sub, stem],
                )
            )
    return prefs, instr


def extract_rrf_docs(
    next_root: Path,
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []

    meta_path = next_root / "docs" / "RRF_META.txt"
    if meta_path.is_file():
        meta = meta_path.read_text(encoding="utf-8", errors="replace")
        # vector() RHS form
        prompt = (
            "In nxt RepRapFirmware macros, how should `vector()` be written on "
            "global / set / var RHS? Give correct and incorrect forms."
        )
        chosen = (
            "Wrap the whole vector() call in curly braces on the RHS:\n"
            "  global nxtPinStates = { vector(min(limits.gpOutPorts, 32), 0.0) }\n"
            "  set global.nxtAbsPos = { vector(var.numAxes, null) }\n"
            "  var averaged = { vector(#move.axes + 1, 0.0) }\n"
            "vector() requires RRF 3.5+. Cap large limits with min(...) to avoid "
            '"too many array elements".'
        )
        rejected = (
            "global nxtPinStates = vector(limits.gpOutPorts, 0.0)\n"
            "; missing braces; uncapped gpOutPorts may overflow array limits"
        )
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="rrf_meta",
                source_path=str(meta_path.relative_to(next_root)),
                tags=["RRF_META", "vector"],
            )
        )

        prompt2 = (
            "In CNC mode (M453), why must nxt macros use `if { … }` / `while { … }` "
            "instead of `if (…)` / `while (…)`?"
        )
        chosen2 = (
            "In CNC mode, `(…)` starts a G-code comment on normal lines unless inside "
            "a braced meta expression. Raw `while (iterations < #spindles)` is parsed "
            "as a comment and breaks control flow. Always write "
            "`while { iterations < #global.nxtProbeResults }` / `if { move.axes[0].homed }`."
        )
        rejected2 = (
            "while (iterations < #spindles)\n"
            "if (move.axes[0].homed)\n"
            "; parentheses are fine at statement level in CNC mode"
        )
        prefs.append(Preference(prompt=prompt2, chosen=chosen2, rejected=rejected2))
        instr.append(
            Instruction(
                prompt=prompt2,
                response=chosen2,
                kind="rrf_meta",
                source_path=str(meta_path.relative_to(next_root)),
                tags=["RRF_META", "cnc_mode"],
            )
        )

        prompt3 = (
            "For user-callable nxt M/G macros on RRF 3.7+, what primary motion-system "
            "guard should appear at the top (and which files must NOT use it)?"
        )
        chosen3 = (
            "Guard secondary inputs with:\n"
            "  if { !inputs[state.thisInput].active }\n"
            "      M99\n"
            "Do not use this in tpre.g, tfree.g, or tpost.g — RRF runs those during Tn "
            "tool changes."
        )
        rejected3 = (
            "Always put the inputs[state.thisInput] guard in every file including "
            "tpre.g / tfree.g / tpost.g."
        )
        prefs.append(Preference(prompt=prompt3, chosen=chosen3, rejected=rejected3))
        instr.append(
            Instruction(
                prompt=prompt3,
                response=chosen3,
                kind="rrf_meta",
                source_path=str(meta_path.relative_to(next_root)),
                tags=["RRF_META", "rrf37"],
            )
        )

    line_path = next_root / "docs" / "RRF_LINE_LENGTH.md"
    if line_path.is_file():
        prompt = (
            "What is the nxt policy for RepRapFirmware macro line length, and how "
            "should a long compound `if` be refactored?"
        )
        chosen = (
            "Keep every non-comment `.g` line ≤ 200 characters (nxt CI policy; RRF "
            "hard-fails near ~255 with 'GCode command too long'). Split long "
            "conditions into `var` booleans, then a short final `if`. Never rely on "
            "line continuation. Verify with `node dist/check-gcode-line-length.mjs`."
        )
        rejected = long_line_antipattern()
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="rrf_meta",
                source_path=str(line_path.relative_to(next_root)),
                tags=["RRF_LINE_LENGTH"],
            )
        )

    ref_path = next_root / "docs" / "RRF_REFERENCE.md"
    if ref_path.is_file():
        ref = ref_path.read_text(encoding="utf-8", errors="replace")
        prompt = (
            "What RepRapFirmware version should developers treat as the evaluation "
            "reference when reviewing nxt macros on the v0.7.0 branch?"
        )
        # Prefer explicit table value if present
        m = re.search(r"\*\*Reference RRF\*\*\s*\|\s*\*\*([^*]+)\*\*", ref)
        ver = m.group(1).strip() if m else "3.7.0-beta.1 (3.7.x line)"
        chosen = (
            f"On branch v0.7.0, treat RepRapFirmware {ver} as the evaluation baseline "
            "for macros, meta G-code, and object-model assumptions. See docs/RRF_REFERENCE.md "
            "and docs/VERSIONING.md (nxt v0.M.0 tracks RRF/DWC 3.M.x)."
        )
        rejected = (
            "Always target RRF 3.3 / pre-vector firmware; nxt does not need 3.5+ features."
        )
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="rrf_meta",
                source_path=str(ref_path.relative_to(next_root)),
                tags=["RRF_REFERENCE"],
            )
        )

    return prefs, instr


def build_dataset(next_root: Path) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []

    code_md = next_root / "docs" / "CODE.md"
    if code_md.is_file():
        p, i = extract_code_md_pairs(
            code_md.read_text(encoding="utf-8", errors="replace"),
            "docs/CODE.md",
        )
        prefs.extend(p)
        instr.extend(i)

    gcode_md = next_root / "GCODE.md"
    if gcode_md.is_file():
        p, i = extract_gcode_md(
            gcode_md.read_text(encoding="utf-8", errors="replace"),
            "GCODE.md",
            next_root,
        )
        prefs.extend(p)
        instr.extend(i)

    p, i = extract_macros(next_root)
    prefs.extend(p)
    instr.extend(i)

    p, i = extract_rrf_docs(next_root)
    prefs.extend(p)
    instr.extend(i)

    p, i = extract_rrf_syntax(next_root)
    prefs.extend(p)
    instr.extend(i)

    p, i = extract_generation_style(next_root)
    prefs.extend(p)
    instr.extend(i)

    p, i = extract_nxt_globals(next_root)
    prefs.extend(p)
    instr.extend(i)

    p, i = extract_macro_skeletons(next_root)
    prefs.extend(p)
    instr.extend(i)

    return dedupe_prefs(prefs), instr


def write_readme(path: Path, *, next_root: Path, n_prefs: int, n_instr: int) -> None:
    body = f"""# RRF / nxt coding dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`), mined from the
**NeXT** (MOS-nxt) repository only.

## Provenance

| Field | Value |
|-------|--------|
| Source repo | `{next_root.resolve()}` (MillenniumMachines/MOS-nxt) |
| License | GPL-3.0 (same as NeXT) |
| Preference rows | {n_prefs} |
| Instruction rows | {n_instr} |

**Included sources:** `docs/CODE.md`, `GCODE.md`, `macros/{{probing,utilities,canned,system}}/**/*.g`,
`docs/RRF_META.txt`, `docs/RRF_LINE_LENGTH.md`, `docs/RRF_REFERENCE.md`, `docs/RRF_3.7_MIGRATION.md`,
`docs/OM_GLOBAL_SIZE.md`, `macros/system/nxt-vars.g`, `ui/src/utils/nxtGlobalsManifest.ts`,
plus generation skeletons from representative macros (G6500, G6512, M6520, nxt.g).

**Pair kinds:** `code_style`, `gcode_api`, `macro`, `rrf_meta`, `rrf_syntax`, `generation_style`,
`nxt_globals`, `macro_skeleton`.

**Excluded:** `AGENT_KNOWLEDGE_BASE.md` (stale fourth-axis notes), `dist/*.zip`,
MillenniumOS / RepRapFirmware sibling trees, DWC Vue/TS as primary train text
(manifest TS is descriptions-only for globals).

## Regenerate

```bash
python -m training.build_rrf_nxt_dataset \\
  --next-root ../NeXT \\
  --out-dir training/datasets/rrf_nxt
```

## Train (dry-run)

```bash
python -m training.train_dpo --dry-run --min-pairs 8 \\
  --preferences training/datasets/rrf_nxt/preferences.jsonl \\
  --out-dir cache/llm_adapters/run_rrf_nxt_v2 \\
  --ollama-base-tag llama3.2:3b \\
  --ollama-tag jarvis-tuned:latest
```

Redistributed derivatives of this dataset inherit GPL-3.0 obligations from NeXT sources.
"""
    path.write_text(body, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    here = Path(__file__).resolve().parent
    repo = here.parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--next-root",
        type=Path,
        default=repo.parent / "NeXT",
        help="Path to NeXT (MOS-nxt) checkout",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=here / "datasets" / "rrf_nxt",
        help="Output directory for JSONL + README",
    )
    p.add_argument(
        "--min-pairs",
        type=int,
        default=8,
        help="Refuse to write if fewer preference pairs (0 to allow empty)",
    )
    args = p.parse_args(argv)

    next_root = args.next_root.resolve()
    if not next_root.is_dir():
        print(f"NeXT root not found: {next_root}", file=sys.stderr)
        return 2
    if not (next_root / "docs" / "CODE.md").is_file() and not (next_root / "GCODE.md").is_file():
        print(
            f"NeXT root does not look like MOS-nxt (missing docs/CODE.md and GCODE.md): {next_root}",
            file=sys.stderr,
        )
        return 2

    prefs, instr = build_dataset(next_root)
    if len(prefs) < args.min_pairs:
        print(
            f"refusing to write: got {len(prefs)} pairs, need >= {args.min_pairs}",
            file=sys.stderr,
        )
        return 3

    out_dir = args.out_dir
    n_p = write_jsonl(out_dir / "preferences.jsonl", (x.to_dict() for x in prefs))
    n_i = write_jsonl(out_dir / "instructions.jsonl", (x.to_dict() for x in instr))
    write_readme(out_dir / "README.md", next_root=next_root, n_prefs=n_p, n_instr=n_i)
    print(f"wrote {n_p} preferences, {n_i} instructions → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
