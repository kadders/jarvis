#!/usr/bin/env python3
"""
Build a CAD/CAM preference dataset for Jarvis DPO.

Sources:
  - training/datasets/cad/STYLE.md (Fusion Design + shop math + Manufacture)
  - docs/operations/fusion360-mcp.md
  - docs/services/tap-collector.md (tool map / chipload / CAM resolve)
  - Curated shop Q&A tuples (fusion tool-use, posts, feeds, Manufacture)

Writes preferences.jsonl + instructions.jsonl for training.train_dpo.
Domain folder name: cad → metadata.domain cad.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from training.dataset_common import (
    Instruction,
    Preference,
    dedupe_prefs,
    extract_code_md_pairs,
    make_fence_re,
    write_jsonl,
)

LICENSE = "proprietary"
TEXT_FENCE = make_fence_re("")

# (kind, prompt, chosen, rejected)
CURATED_QA: Sequence[Tuple[str, str, str, str]] = (
    (
        "fusion_tool_use",
        "Fusion MCP timed out on create_box. What should I do next?",
        "Call get_scene_info to see if the box already exists, then retry a single mutation only if needed. Prefer parallel_tool_calls=false.",
        "Immediately re-fire create_box, extrude, and fillet in parallel without checking the scene.",
    ),
    (
        "fusion_tool_use",
        "How should an 8B Jarvis model talk to Fusion 360 CAD from Cursor?",
        "Use Jarvis /v1 for tool_calls and Cursor's fusion360-mcp-server for execution. Start from MCP prompts (e.g. create-box); one Fusion op per tool call.",
        "Host all Fusion CAD tools inside jarvis_mcp and dump every schema on every turn.",
    ),
    (
        "fusion_tool_use",
        "Call the Fusion health check before editing.",
        "Call ping, expect ok/pong, then get_scene_info before the first mutation.",
        "Skip ping; invent component names and call create_box immediately.",
    ),
    (
        "fusion_tool_use",
        "User asks to add a 20x20x10 mm box in Fusion via MCP.",
        "Emit a single create_box tool_call with explicit dimensions (respect document units). Wait for the tool result before further mutations.",
        "Reply in prose that the box was created without emitting tool_calls.",
    ),
    (
        "fusion_tool_use",
        "Should Fusion mutations run in parallel tool_calls?",
        "No. Fusion allows one operation per tool call; prefer parallel_tool_calls=false and sequential mutations.",
        "Yes. Batch every sketch, extrude, and fillet in one parallel_tool_calls turn.",
    ),
    (
        "fusion_timeout",
        "Fusion MCP returns TIMEOUT. Is the document state known?",
        "Unknown until get_scene_info (or equivalent). Upstream does not auto-retry; inspect before mutating again.",
        "Assume TIMEOUT means nothing changed and always re-apply the same mutation blindly.",
    ),
    (
        "fusion_tool_use",
        "Where does the Fusion TCP bridge listen and what auth does it have?",
        "Fusion360MCP add-in listens on TCP 9876 with no authentication — trusted LAN only; firewall to the agent host.",
        "Expose port 9876 publicly; the bridge uses OAuth so it is safe on the internet.",
    ),
    (
        "scene_recovery",
        "Before filleting an existing body, what should the agent do?",
        "Call get_scene_info to learn body/component names and units, then issue one fillet tool call.",
        "Guess Body1 / Component1 names and fillet without reading the scene.",
    ),
    (
        "tool_map",
        "How do RRF tool numbers relate to Fusion tools in this shop?",
        "RRF Tn maps one-to-one to Fusion tool library post-process.number. Permanent identity is the Fusion GUID; tap-collector resolves GUID when unambiguous.",
        "RRF T-number is the Fusion GUID; post-process.number can be ignored.",
    ),
    (
        "tool_map",
        "Two catalog tools share the same post-process.number after import. What happens?",
        "Default import treats the upload as source of truth: the older conflicting GUID for that number is removed (replaced_duplicates).",
        "Keep both GUIDs forever with the same post-process.number and pick randomly at cut time.",
    ),
    (
        "chipload",
        "Where do feed_mm_min and chipload_mm on a tap segment come from?",
        "From the Fusion tool library start-values presets (v_f, f_z, n) stamped when the tool GUID resolves — library estimates, not live encoder feeds.",
        "They are always measured live from the spindle encoder regardless of Fusion presets.",
    ),
    (
        "chipload",
        "May the LLM invent chipload for 6061 aluminum when Fusion presets are missing?",
        "No. Never invent feed or chipload from the material registry. Missing Fusion feed → cutting_force null with missing_tool_cutting_preset.",
        "Yes. Use a standard 0.05 mm/tooth for all 6061 tools when presets are missing.",
    ),
    (
        "chipload",
        "How is effective chipload computed for a cut?",
        "fz_eff = feed_mm_min / (n_teeth * RPM). Compare to Fusion f_z and report mismatch %; do not silently overwrite the library target.",
        "Always report Fusion f_z as actual chipload and ignore F/(Nt*RPM).",
    ),
    (
        "cutting_force",
        "What is the cutting-force estimate used for?",
        "An engineering envelope over WoC ~1.2–1.5 mm and DoC ≤ 4.5 mm using Fusion feed/chipload plus material Ks. Advisory only — not a machine command.",
        "A closed-loop spindle power setpoint pushed automatically to the CNC.",
    ),
    (
        "cutting_force",
        "Which materials have force coefficients in tap-collector?",
        "Default 6061 aluminum; selectable 7075 aluminum and A36 steel. Coefficients affect force only, never invent feeds.",
        "Any material name invents both Ks and feeds from a web search at runtime.",
    ),
    (
        "cam_gcode_resolve",
        "How does tap-collector resolve a CAM job file?",
        "Strip drive/gcodes prefixes; look up by basename under the CAM NFS share (RRF gcodes → /mnt/CAM/). Optional sha256. Summarize tools/feeds/G0-G1 — not full path simulation.",
        "Fully simulate modal G0–G3 geometry and work offsets for every file today.",
    ),
    (
        "post_hygiene",
        "What should an nxt Fusion post do for probing/setup?",
        "Call documented nxt macros (M98 / custom G/M), keep RRF line limits, and preserve tool numbers matching post-process.number.",
        "Inline multi-kilobyte probing macros into every posted toolpath file.",
    ),
    (
        "post_hygiene",
        "May analytics rewrite G-code and upload it to the machine?",
        "No. Advisory only — suggest RPM bands or M593 notes. Never push modified G-code programs to the machine from Jarvis or tap-collector.",
        "Yes. Auto-edit S/F words and upload the new program to the Duet after vibration analysis.",
    ),
    (
        "manufacture_setup",
        "Order of operations for Fusion Manufacture before posting?",
        "Define setup (WCS, stock, machine), assign tools with correct post-process.number, generate ops, simulate, then post. Re-export tool library to tap-collector after feed edits.",
        "Post first, invent WCS later, and never sync the tool library.",
    ),
    (
        "toolpath",
        "When to prefer adaptive clearing vs contour?",
        "Adaptive for bulk removal with controlled engagement; contour/finish for walls and final dimensions. Adaptive does not replace every finish or drill op.",
        "Always use adaptive clearing for finishing, engraving, and drilling.",
    ),
    (
        "toolpath",
        "Should Jarvis invent a full pocket G-code program in chat?",
        "No. Coach strategy in words; generate toolpaths in Fusion Manufacture (or an explicit CAM tool). Do not invent G1 clouds as a posted program substitute.",
        "Yes. Emit a complete invented G-code pocket and tell the operator to send it to the mill.",
    ),
    (
        "manufacture_setup",
        "Operator wants simultaneous 5-axis adaptive but the shop is 3-axis Milo/nxt.",
        "Default coaching assumes 3-axis unless rotary is specified. Do not invent 5-axis posts the shop does not run; ask before recommending multi-axis strategies.",
        "Always recommend simultaneous 5-axis adaptive with a fictional post.",
    ),
    (
        "toolpath",
        "Tap-collector reports avoid_rpm bands near tooth-pass resonance. What should coaching say?",
        "Prefer advisory avoid bands / M593 suggestions over inventing spindle speeds that sit on resonance peaks.",
        "Ignore avoid_rpm; pick the highest RPM the spindle brochure lists.",
    ),
    (
        "fusion_tool_use",
        "What feedback domain should Fusion Design trajectories use?",
        "metadata.domain = cad (formal Learning UI / few-shot slice). Log via jarvis_log_outcome after CAD sessions.",
        "metadata.domain = python_jarvis for all Fusion MCP work.",
    ),
    (
        "manufacture_setup",
        "After changing feeds in Fusion Manufacture, what else must happen?",
        "Re-export .tools/JSON and re-import into tap-collector so GUID, post-process.number, feed_mm_min, and chipload_mm stay aligned with the machine.",
        "Change feeds only at the CNC and leave Fusion/tap catalogs stale.",
    ),
    (
        "tool_map",
        "Segment has rrf_slot=3 but two Fusion GUIDs claim post-process.number 3. What should the collector do?",
        "Do not invent a GUID; leave resolution unambiguous only when catalog mapping is unique. Fix the library (import replaces duplicates) before trusting stamps.",
        "Pick a random GUID and stamp feed/chipload anyway.",
    ),
    (
        "cam_gcode_resolve",
        "Is path geometry from G-code available in the tap UI today?",
        "No. Summary stats only (tools used, spindle/feed command stats, G0/G1 counts). Full CAM simulation is future work.",
        "Yes. The UI always renders full toolpath geometry from every G-code file.",
    ),
    (
        "fusion_tool_use",
        "User says: create a sketch and extrude it 5mm in one go via Fusion MCP.",
        "Split into sequential tool calls: create/select sketch, add geometry, then extrude after each success. One Fusion op per call.",
        "Batch create_sketch and extrude_feature in one parallel tool_calls message.",
    ),
    (
        "chipload",
        "What fields does a Fusion tool library entry store for cutting presets?",
        "feed_mm_min (v_f), chipload_mm (f_z), reference_rpm (n), plus geometry (DC, NOF), post-process.number, and GUID.",
        "Only a display name; feeds are looked up from Wikipedia at cut time.",
    ),
    (
        "cutting_force",
        "DoC requested above 4.5 mm for the force envelope. Valid?",
        "Force envelope rejects DoC above 4.5 mm (DOC_MAX_MM). Do not invent forces outside the documented sweep.",
        "Extrapolate linearly past 4.5 mm and report a force anyway.",
    ),
    (
        "post_hygiene",
        "Where do posted Fusion programs live relative to the machine?",
        "Fridge CAM share mounted read-only for tap-collector; machine path is flat under gcodes → /mnt/CAM/. Resolve by basename.",
        "Jarvis writes posts directly into the Duet flash over MQTT.",
    ),
    (
        "manufacture_setup",
        "How should Manufacture WCS relate to nxt probing?",
        "Align Fusion Manufacture WCS with how nxt probing macros establish work offsets (e.g. G54). Confirm origin/axes before posting.",
        "Use a random WCS that conflicts with probed offsets; the controller will reconcile.",
    ),
    (
        "fusion_tool_use",
        "May the assistant invent a Fusion tool name not in the MCP list?",
        "No. Only call tools Fusion MCP exposes. If unsure, ping/get_scene_info or list available tools — do not invent names.",
        "Invent fusion_auto_design_v2 when the real tool list is inconvenient.",
    ),
    (
        "toolpath",
        "Closed-loop: high vibration — should Jarvis rewrite adaptive feeds in Fusion automatically?",
        "No. Advise the operator with suggested bands; do not silently rewrite Manufacture ops or push new posts to the controller.",
        "Yes. Auto-edit Fusion adaptive feeds and re-post to the Duet without confirmation.",
    ),
    (
        "chipload",
        "Inch tool imported into a metric catalog — chipload handling?",
        "Convert inch library values to mm on import. chipload_mm is mm/tooth. State units when discussing with operators.",
        "Paste inch/tooth into chipload_mm without conversion.",
    ),
    (
        "fusion_tool_use",
        "Why prefer Fusion MCP prompts over attaching all schemas?",
        "Upstream exposes ~90 tools; full schemas drown an 8B model. Start from prompts like create-box, expand only when needed.",
        "Always attach all ~90 schemas so the model has maximum choice every turn.",
    ),
    (
        "cam_gcode_resolve",
        "What does a G-code summary include today?",
        "Tools used, tool-change byte offsets, spindle/feed command stats, G0/G1 counts — not path geometry or full modal simulation.",
        "Full cutting-vs-rapid timeline with 3D path reconstruction for every file.",
    ),
    (
        "manufacture_setup",
        "Holder missing on a tool library entry used for deep pockets. Advice?",
        "Fill holder/stickout consistent with the physical tool before relying on collision or force estimates; blank holder is unsafe for deep pockets.",
        "Leave holder blank; assume infinite stickout is fine.",
    ),
    (
        "tool_map",
        "tpost.g publishes tool MQTT events. What identity should analytics prefer?",
        "Keep RRF slot on the event, resolve Fusion GUID via post-process.number when unambiguous, and stamp library feed/chipload from that GUID.",
        "Drop RRF slot and keep only a guessed tool name string.",
    ),
    (
        "fusion_tool_use",
        "Document units are mm. User asks for a 1 inch cube via create_box.",
        "Convert or pass dimensions consistent with document units (25.4 mm cube) and state units in the tool args/result summary.",
        "Always send inches regardless of document units and hope Fusion guesses.",
    ),
)


DOC_TIP_PATTERNS: Sequence[Tuple[str, re.Pattern[str], str, str]] = (
    (
        "fusion_tool_use",
        re.compile(
            r"one operation per tool call|parallel_tool_calls",
            re.I,
        ),
        "Fusion allows one operation per tool call; prefer parallel_tool_calls=false.",
        "Batch many Fusion mutations in one parallel tool_calls turn.",
    ),
    (
        "fusion_timeout",
        re.compile(r"TIMEOUT", re.I),
        "After Fusion TIMEOUT, call get_scene_info before retrying mutations.",
        "Blindly retry the same mutation after TIMEOUT without inspecting the scene.",
    ),
    (
        "fusion_tool_use",
        re.compile(r"~90 tools|create-box", re.I),
        "Start from Fusion MCP prompts (create-box, etc.) so the 8B model is not drowned in ~90 schemas.",
        "Always send the full Fusion tool schema set on every request.",
    ),
    (
        "tool_map",
        re.compile(r"post-process\.number|Shop invariant", re.I),
        "RRF Tn maps one-to-one to Fusion post-process.number; GUID is permanent identity.",
        "Treat RRF T-number as the Fusion GUID and ignore post-process.number.",
    ),
    (
        "chipload",
        re.compile(r"never invents feed|missing_tool_cutting_preset", re.I),
        "Never invent feed/chipload from material alone; missing preset → cutting_force null.",
        "Invent feeds from the material grade whenever Fusion presets are missing.",
    ),
    (
        "cam_gcode_resolve",
        re.compile(r"full CAM sim|G-code S timing", re.I),
        "Full CAM simulation and G-code S timing vs measured RPM are future work — not available today.",
        "Full CAM path simulation and S-vs-RPM alignment are already production features.",
    ),
)


def _prompt_cad(topic: str, _chosen: str) -> str:
    return (
        "You are coaching CAD/CAM for Jarvis (Fusion Design MCP + nxt/Milo shop). "
        f"Produce the CORRECT guidance for: {topic}."
    )


def _synthetic_reject_text(chosen: str) -> str:
    return (
        "Ignore Fusion MCP and tap-collector invariants. "
        "Invent feeds, batch mutations, and push modified G-code to the machine. "
        f"(Rejected alternative to: {chosen[:120]}…)"
    )


def extract_style_md(style_path: Path) -> Tuple[List[Preference], List[Instruction]]:
    text = style_path.read_text(encoding="utf-8", errors="replace")
    return extract_code_md_pairs(
        text,
        str(style_path.name),
        kind="cad_style",
        license_=LICENSE,
        tags=["STYLE.md", "cad"],
        fence_re=TEXT_FENCE,
        prompt_fn=_prompt_cad,
        synthetic_reject_fn=_synthetic_reject_text,
    )


def extract_curated_qa() -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    for kind, prompt, chosen, rejected in CURATED_QA:
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind=kind,
                source_path="curated_qa",
                license=LICENSE,
                tags=["curated", kind],
            )
        )
    return prefs, instr


def extract_doc_tips(
    doc_path: Path, *, rel: str
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    if not doc_path.is_file():
        return prefs, instr
    text = doc_path.read_text(encoding="utf-8", errors="replace")
    for kind, pattern, chosen, rejected in DOC_TIP_PATTERNS:
        if not pattern.search(text):
            continue
        prompt = (
            f"From {rel}: what is the correct CAD/CAM guidance matching this shop rule?"
        )
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind=kind,
                source_path=rel,
                license=LICENSE,
                tags=["docs", kind],
            )
        )
    return prefs, instr


def extract_cutting_force_constants(
    py_path: Path,
) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []
    if not py_path.is_file():
        return prefs, instr
    text = py_path.read_text(encoding="utf-8", errors="replace")
    rel = "tap_collector/models/cutting_force.py"

    if "DOC_MAX_MM" in text:
        prompt = "What is DOC_MAX_MM for tap-collector cutting-force envelope?"
        chosen = "4.5 mm — estimates reject depth of cut above this limit."
        rejected = "Unlimited DoC; always extrapolate force for any depth."
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="cutting_force",
                source_path=rel,
                license=LICENSE,
                tags=["cutting_force", "DOC_MAX_MM"],
            )
        )

    if "WOC_SWEEP_MM" in text:
        prompt = "What WoC sweep does cutting_force use (mm)?"
        chosen = "About 1.2, 1.35, and 1.5 mm (WOC_SWEEP_MM)."
        rejected = "Only full-diameter slotting; no WoC sweep."
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="cutting_force",
                source_path=rel,
                license=LICENSE,
                tags=["cutting_force", "WOC_SWEEP_MM"],
            )
        )

    if "6061 aluminum" in text:
        prompt = "Default material for cutting-force coefficients?"
        chosen = "6061 aluminum (selectable 7075 aluminum / A36 steel). Affects Ks only — never invents feed."
        rejected = "Default to mystery plastic and invent both Ks and feeds."
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="cutting_force",
                source_path=rel,
                license=LICENSE,
                tags=["cutting_force", "material"],
            )
        )

    if "fz_eff" in text or "feed_mm_min / (n_teeth" in text:
        prompt = "How does cutting_force compute effective chipload?"
        chosen = "fz_eff = feed_mm_min / (n_teeth * rpm); optional mismatch % vs Fusion chipload."
        rejected = "fz_eff is always copied from material hardness tables."
        prefs.append(Preference(prompt=prompt, chosen=chosen, rejected=rejected))
        instr.append(
            Instruction(
                prompt=prompt,
                response=chosen,
                kind="chipload",
                source_path=rel,
                license=LICENSE,
                tags=["chipload", "fz_eff"],
            )
        )

    return prefs, instr


def build_dataset(jarvis_root: Path) -> Tuple[List[Preference], List[Instruction]]:
    prefs: List[Preference] = []
    instr: List[Instruction] = []

    style = jarvis_root / "training" / "datasets" / "cad" / "STYLE.md"
    if style.is_file():
        p, i = extract_style_md(style)
        prefs.extend(p)
        instr.extend(i)

    p, i = extract_curated_qa()
    prefs.extend(p)
    instr.extend(i)

    for rel in (
        "docs/operations/fusion360-mcp.md",
        "docs/services/tap-collector.md",
    ):
        p, i = extract_doc_tips(jarvis_root / rel, rel=rel)
        prefs.extend(p)
        instr.extend(i)

    p, i = extract_cutting_force_constants(
        jarvis_root / "tap_collector" / "models" / "cutting_force.py"
    )
    prefs.extend(p)
    instr.extend(i)

    prefs = dedupe_prefs(prefs)
    # Keep instructions aligned with surviving prefs by prompt
    keep = {p.prompt for p in prefs}
    instr = [x for x in instr if x.prompt in keep]
    return prefs, instr


def write_readme(path: Path, *, n_prefs: int, n_instr: int) -> None:
    body = f"""# CAD / CAM dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`). Formal feedback domain: **`cad`**.

Covers Fusion Design MCP tool-use, shop math (chipload / tool GUID↔Tn), posts hygiene, and Manufacture coaching boundaries.

| Field | Value |
|-------|--------|
| Preference rows | {n_prefs} |
| Instruction rows | {n_instr} |
| License | proprietary (Jarvis STYLE + docs) |

## Regenerate

```bash
python -m training.build_cad_dataset \\
  --jarvis-root . \\
  --out-dir training/datasets/cad
```

## Golden eval

```bash
python -m training.eval_golden \\
  --cases training/golden/cad.jsonl \\
  --base-url http://llm.jarvis.lan/v1 \\
  --model llama31-8b \\
  --api-key jarvis-lan-openai
```
"""
    path.write_text(body, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    here = Path(__file__).resolve().parent
    repo = here.parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jarvis-root", type=Path, default=repo)
    p.add_argument("--out-dir", type=Path, default=here / "datasets" / "cad")
    p.add_argument("--min-pairs", type=int, default=40)
    args = p.parse_args(argv)

    prefs, instr = build_dataset(args.jarvis_root.resolve())
    if len(prefs) < args.min_pairs:
        print(
            f"refusing to write: got {len(prefs)} pairs, need >= {args.min_pairs}",
            file=sys.stderr,
        )
        return 3

    out_dir = args.out_dir
    n_p = write_jsonl(out_dir / "preferences.jsonl", (x.to_dict() for x in prefs))
    n_i = write_jsonl(out_dir / "instructions.jsonl", (x.to_dict() for x in instr))
    write_readme(out_dir / "README.md", n_prefs=n_p, n_instr=n_i)
    print(f"wrote {n_p} preferences, {n_i} instructions → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
