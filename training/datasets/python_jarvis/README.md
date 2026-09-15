# Python / Jarvis coding dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`).

| Field | Value |
|-------|--------|
| Preference rows | 61 |
| Instruction rows | 63 |
| Formatter | Black 88 / py310 |

## Regenerate

```bash
python -m training.build_python_dataset \
  --jarvis-root . \
  --tap-testing-root ../tap-testing \
  --tiny-ocr-root ../tiny-ocr \
  --out-dir training/datasets/python_jarvis
```
