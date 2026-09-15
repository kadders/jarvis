# Jarvis Wand Service

Interactive wand gesture + incantation fusion for the Jarvis stack.

## Run locally

```bash
cd wand
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8002 --reload
```

## Compose

```bash
docker compose up -d wand_service
curl -s http://127.0.0.1:8002/health
```

## Train gesture SVM

```bash
cd wand
python training/generate_synthetic.py
python training/train_svm.py
# point WAND_GESTURE_MODEL_PATH at cache/models/wand_gesture
```

## Edge

- Pi NoIR: [`edge/pi/README.md`](edge/pi/README.md)
- ESP32: [`edge/esp32/README.md`](edge/esp32/README.md)

## Catalog

- Ontology: `data/ontology/spells.json`
- Park gestures: `data/park/gestures.json`
- YouTube manifest: `data/clips/manifest.yaml`
- Validate: `python jobs/catalog/validate_clips.py --write`
