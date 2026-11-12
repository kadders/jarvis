# CAD / CAM dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`). Formal feedback domain: **`cad`**.

Covers Fusion Design MCP tool-use, shop math (chipload / tool GUID↔Tn), posts hygiene, and Manufacture coaching boundaries.

| Field | Value |
|-------|--------|
| Preference rows | 88 |
| Instruction rows | 88 |
| License | proprietary (Jarvis STYLE + docs) |

## Regenerate

```bash
python -m training.build_cad_dataset \
  --jarvis-root . \
  --out-dir training/datasets/cad
```

## Golden eval

```bash
python -m training.eval_golden \
  --cases training/golden/cad.jsonl \
  --base-url http://llm.jarvis.lan/v1 \
  --model llama31-8b \
  --api-key jarvis-lan-openai
```
