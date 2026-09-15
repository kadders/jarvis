# Wand API

Interactive wand service (`wand/`) on port **8002**.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness |
| POST | `/api/v1/recognize` | Fuse trajectory + optional transcript → spell / cast |
| GET | `/api/v1/spells` | Ontology + park gesture metadata |
| GET | `/api/v1/catalog/manifest` | YouTube clip manifest |
| WS | `/api/v1/sessions/live` | Live point stream + recognize |
| GET | `/api/v1/effects/last` | Last `spell_cast` for edge FX pollers |
| POST | `/api/v1/effects/ack` | Edge effect acknowledgement |

## Recognize body

```json
{
  "points": [{"t": 0, "x": 0.2, "y": 0.2, "confidence": 1.0}],
  "transcript": "incendio",
  "gesture_complete": true,
  "session_id": "optional"
}
```

Coordinates are normalized `[0,1]` with **y up**. Fusion: `α·P(gesture)+β·P(audio)`; gesture alone can cast when complete (park-like).

## Env

See [environment variables](../reference/environment-variables.md) (`WAND_*`).
