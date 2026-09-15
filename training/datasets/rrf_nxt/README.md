# RRF / nxt coding dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`), mined from the
**NeXT** (MOS-nxt) repository only.

## Provenance

| Field | Value |
|-------|--------|
| Source repo | `/media/kad/data/repositories/NeXT` (MillenniumMachines/MOS-nxt) |
| License | GPL-3.0 (same as NeXT) |
| Preference rows | 316 |
| Instruction rows | 316 |

**Included sources:** `docs/CODE.md`, `GCODE.md`, `macros/{probing,utilities,canned,system}/**/*.g`,
`docs/RRF_META.txt`, `docs/RRF_LINE_LENGTH.md`, `docs/RRF_REFERENCE.md`.

**Excluded:** `AGENT_KNOWLEDGE_BASE.md` (stale fourth-axis notes), `dist/*.zip`,
MillenniumOS / RepRapFirmware sibling trees, DWC Vue/TS as primary train text.

## Regenerate

```bash
python -m training.build_rrf_nxt_dataset \
  --next-root ../NeXT \
  --out-dir training/datasets/rrf_nxt
```

## Train (dry-run)

```bash
python -m training.train_dpo --dry-run --min-pairs 8 \
  --preferences training/datasets/rrf_nxt/preferences.jsonl \
  --out-dir cache/llm_adapters/run_rrf_nxt
```

Redistributed derivatives of this dataset inherit GPL-3.0 obligations from NeXT sources.
