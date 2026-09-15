# Wand service

Jarvis **`wand/`** recognizes Interactive Wand–style casts: IR tip trajectories + spoken incantations → `spell_cast` events for LED/relay/SFX.

## Topology

- **Service:** FastAPI on **8002** (`jarvis_wand_service`)
- **Edge Pi:** OpenCV blob track (`wand/edge/pi/`)
- **Edge ESP32:** FX poller (`wand/edge/esp32/`)
- **ASR:** optional transcript into recognize (reuse `asr/` WS)
- **Models:** heuristic templates or sklearn SVM under `cache/models/wand_gesture`

## Docs

- [API](../api/wand.md)
- [Optics bring-up](optics.md)
- [Spell catalog](spell-catalog.md)
