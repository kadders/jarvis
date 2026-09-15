# Arduino / ESP32 coding dataset

Preference pairs for Jarvis DPO/QLoRA (`training.train_dpo`).

| Field | Value |
|-------|--------|
| Preference rows | 9 |
| Instruction rows | 9 |

## Regenerate

```bash
python -m training.build_arduino_dataset \
  --jarvis-root . \
  --tiny-ocr-root ../tiny-ocr \
  --out-dir training/datasets/arduino_wand
```
