# Klipper / Moonraker telemetry (future)

Phase 4 adapter for 3D printer telemetry aligned with the shared **motion** envelope used by CNC tap-testing.

## Topic namespace

| Topic | Publisher | Notes |
|-------|-----------|-------|
| `print/{device}/motion` | Moonraker bridge (lab) | Toolhead XYZ; map extruder `e` separately |
| `print/{device}/session` | Bridge | Start/stop aligned with print job |
| `print/{device}/status` | Bridge | Heater/state context |

Jarvis **tap_collector** ingests `print/#` motion today (`source: moonraker`) into the same `motion_samples` timeline as `tap/…/motion`.

## Shared motion envelope

Same fields as [Tap collector — motion](tap-collector.md#motion_topic): `schema_version`, `session_id`, `t_s`, `x/y/z`, optional `a`, `source`, `feed_mm_min`, `file_position`.

## Moonraker bridge (not shipped)

Recommended lab bridge:

1. Subscribe Moonraker WebSocket `notify_status_update` for toolhead position.
2. Publish `print/{device}/motion` at 2–10 Hz with delta filter (reuse tap-testing `MotionPublishFilter` pattern).
3. Session boundaries from print start/finish events.

## SPA

The tap-collector replay sim panel renders any session with `motion_samples` regardless of `source` (`tap_rrf_poll`, `cam_sim`, `moonraker`).

## Durable log

**telemetry_bridge** includes `print/#` in MQTT subscribe filters; motion rows land in `{telemetry_log}/{session_id}/motion.jsonl` and Kafka topic `jarvis.telemetry.tap` for replay via `POST /api/v1/sessions/{id}/replay`.
