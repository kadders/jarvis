# Spell catalog

Catalog layers for training and ASR lexicon. **No iTunes / FairPlay ingest.**

## Layers

| Layer | Path / tool | Role |
|-------|-------------|------|
| A Park gestures | `wand/data/park/gestures.json` | Gold shapes for station |
| B Ontology | `wand/data/ontology/spells.json` | Incantation ↔ `spell_id` |
| C Manual notes | (operator) | Optional Apple TV watch notes |
| D YouTube clips | `wand/data/clips/manifest.yaml` | Film motion + ASR enrichment |
| E Local DRM-free | folder of unprotected files | Same pipeline as D |

## Baseline YouTube validation

```bash
cd wand
# requires yt-dlp on PATH
python jobs/catalog/validate_clips.py --write
# optional download of accepted/candidate seeds
python jobs/catalog/ingest_youtube.py --download --limit 3
```

Scorecard: `wand/data/clips/validation-notes.md`

### Acceptance (≥6/10)

Trust, duration, audio, motion, focus (0–2 each). Prefer `WizardingWorld` / `Harry Potter` channels.

### Seed clips

| spell | video_id |
|-------|----------|
| expecto_patronum | `RezjC4jdbTU`, `RCVdgFin8uQ` |
| wingardium_leviosa | `Qgr4dcsY-60` |
| expelliarmus | `fEOuMCd5e9I` |

Station gold labels remain **park shapes + Pi recordings**; YouTube is enrichment.
