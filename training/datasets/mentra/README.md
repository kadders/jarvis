# MentraOS dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`). Formal feedback domain: **`mentra`**.

Covers Mentra Miniapp SDK (`@mentra/miniapp`) and Mentra Bluetooth SDK coaching from docs.mentraglass.com. OEM engine APIs deferred.

| Field | Value |
|-------|--------|
| Preference rows | 61 |
| Instruction rows | 61 |
| License | proprietary (Jarvis STYLE + curated Mentra docs Q&A) |

## Regenerate

```bash
python -m training.build_mentra_dataset \
  --jarvis-root . \
  --out-dir training/datasets/mentra
```

## Golden eval

```bash
python -m training.eval_golden \
  --cases training/golden/mentra.jsonl \
  --base-url http://llm.jarvis.lan/v1 \
  --model llama31-8b \
  --api-key jarvis-lan-openai
```
