# Tap collector (MQTT telemetry from tap-testing)

Ingests optional MQTT telemetry from the sibling **tap-testing** project (Raspberry Pi ADXL345 tap tests), buffers accel batches, runs live spectrum + session-end analysis, and can forward summaries to the Jarvis LLM API.

Also serves a **telemetry SPA** at **`/ui/`** for importing historical **live_spindle / homing** CSVs (or run ZIPs with sidecars), assigning **tool segments**, and viewing time series, spectrum, and **RPM-domain resonance analytics**.

## Run (Compose profile)

```bash
docker compose --profile telemetry up -d mosquitto tap_collector
curl -s http://localhost:8004/health
# open http://localhost:8004/ui/
```

On the Pi (tap-testing), **prefer RRF SBC / DSF mode** (MQTT is gated on `rr_model.sbc` by default):

```bash
pip install paho-mqtt
export TAP_RRF_BASE=http://127.0.0.1   # or milo.local
export TAP_MQTT_HOST=mqtt.jarvis.lan
export TAP_MQTT_PORT=1883
export TAP_MQTT_DEVICE_ID=milo
python -m tap_testing.run_cycle --no-plot
```

Use `TAP_MQTT_FORCE=1` only for lab benches without a Duet SBC. Topic contract: see tap-testing `docs/MQTT_TELEMETRY.md`.

## Topics subscribed (collector)

Default filters (`TAP_COLLECTOR_MQTT_TOPICS`): **`tap/#,duet/#,cam/#`**.

### From tap-testing (`tap/#`)

| Topic | Role |
|-------|------|
| `tap/{device_id}/session` | lifecycle: `event` `start`/`stop` plus additive `event_type` `start_session`/`end_session` (one parent `session_id`) |
| `tap/{device_id}/accel/batch` | batched accel windows under the parent session |
| `tap/{device_id}/tool` | enriched tool timeline (`event` `start`/`change`/`stop`; `event_type` `tool_selected` / `tool_offset_changed` / …) |
| `tap/{device_id}/analysis` | Pi or Jarvis analysis summaries |
| `tap/{device_id}/modbus` | optional VFD register rows (homing-GUI leftover path) |
| `tap/{device_id}/spindle` | ArborCTL live Hz / RPM / optional load (decoded OM values, not raw Modbus) |
| `tap/{device_id}/motion` | Structured XYZ(A) machine position from Pi `rr_model` poll (replay / sim UI) |
| `tap/{device_id}/video_overlay` | Pre-rendered HUD **text** for **tap-stream** ffmpeg (not used for sim UI) |
| `tap/{device_id}/status` | publisher liveness (`recording`/`idle`/`error`, includes `session_id`) |

Canonical wire format for `tap/#` topics: sibling **tap-testing** repo `docs/MQTT_PAYLOAD_REFERENCE.md`.

### Motion topic {#motion_topic}

**`tap/{device_id}/motion`** (QoS 0) carries structured machine coordinates for session replay and the SPA sim panel. Published by tap-testing `live_spindle_service._rrf_poll_loop` using the same `axis_positions_mm` as the video HUD — **not** parsed from `video_overlay` text.

| Field | Required | Notes |
|-------|----------|-------|
| `schema_version` | yes | `1` |
| `session_id`, `device_id`, `t_s` | yes | `t_s` = seconds since recording start (ADXL origin) |
| `x`, `y`, `z` | yes | mm (`userPosition` preferred) |
| `a` | optional | 4th-axis degrees when present |
| `source` | yes | `tap_rrf_poll` (CAM sim: `cam_sim`; Klipper: `moonraker`) |
| `feed_mm_min`, `file_position`, `rrf_status`, `tool_number` | optional | Context for replay |

Collector stores capped **`motion_samples`** on the session (default max **10000**, `TAP_COLLECTOR_MOTION_SAMPLES_MAX`). **`GET /api/v1/sessions/{id}/motion`** returns a downsampled polyline + bbox. Live WebSocket frames include `latest_motion` / tail of `motion_samples`.

**`cam/{device_id}/motion`** (future CAM sim) uses the same envelope with `source: cam_sim`.

**Do not conflate** with **`video_overlay`** — that topic is for cluster **tap-stream** YouTube overlay only ([tap-stream](tap-stream.md)).

### From 4th-combinator CAM sim (`cam/#`)

Milo-Code **4th-combinator** embeds `M118 P6 T"cam/{device}/…" S"…"` in sim G-code (same payload shape as tap-testing). **Do not** configure RRF to subscribe to `cam/#`.

| Topic | Role |
|-------|------|
| `cam/{device_id}/session` | sim lifecycle (`mode: "sim"`); `start_session` / `end_session` |
| `cam/{device_id}/tool` | schema v2 `tool_selected` at each simulated tool change |
| `cam/{device_id}/motion` | commanded XYZ(A) during sim (same envelope as `tap/…/motion`) |
| `cam/{device_id}/status` | publisher `connected` / `idle` |

Sessions get `origin=cam` and `job_file_source=cam`. Job enrichment precedence: **`tap` > `cam` > `duet`**.

Smoke without the mill:

```bash
mosquitto_pub -h mqtt.jarvis.lan -t cam/milo/session -m \
  '{"event":"start","event_type":"start_session","session_id":"sim-1","mode":"sim","job_file":"part.gcode","tool_number":3}'
curl -s http://tap.jarvis.lan/api/v1/sessions | jq .
```

### From Duet RRF macros (`duet/#`)

RRF **3.6+** does **not** auto-publish the object model over MQTT. Macros publish with `M118 L6 T"topic" S"…"`. **Do not** configure RRF to subscribe to `tap/#` — subscribe only shows DWC messages and does not run G-code.

| Topic | Role |
|-------|------|
| `duet/{machine}/tool` | Tool change (`event`, `tool_number`, `previous_tool`, optional `session_id`, `job_file`, `file_position`) |
| `duet/{machine}/job` | Job `start` / `stop` / `pause` / `resume` + `job_file` / `status` |
| `duet/{machine}/state` | Snapshot (`status`, `current_tool`, `job_file`, `file_position`) |
| `duet/{machine}/message` | Free text or JSON logged on the session |

Session binding: explicit `session_id` → active live session for that machine → create `duet-{machine}-{YYYYMMDD}` (`origin=duet`).

Enable MQTT on the Duet (**publish only**):

```gcode
; config.g / dsf-config.g — no S= subscribe to tap topics
M586.4 C"milo"
M586 P4 H"192.168.86.4" R1883 S1
```

Example tool publish (e.g. from `tpost.g`; adjust meta-G syntax for your firmware):

```gcode
M118 P6 T"duet/milo/tool" S"{""event"":""change"",""tool_number"":3,...}"
```

Smoke without Duet:

```bash
mosquitto_pub -h mqtt.jarvis.lan -t duet/milo/tool -m \
  '{"event":"change","tool_number":3,"previous_tool":1,"job_file":"part.gcode"}'
curl -s http://tap.jarvis.lan/api/v1/sessions | jq .
```

### Tool timeline

`tap/{device_id}/tool` (from tap-testing), `cam/{device_id}/tool` (from 4th-combinator sim), and `duet/{machine}/tool` (from RRF macros) are stored per session as full `tool_events` (schema v2 payloads kept intact — not reduced to name/number). `tool_number` / `rrf_slot` is the RRF machine slot; permanent Fusion identity is resolved via the tool library. Session detail returns `tool_events`, `tools_snapshot(_history)`, `status_events`, `job_file`, `recording_state`, and `n_tool_events`.

**Shop invariant:** RRF `Tn` maps one-to-one to Fusion `post-process.number`. The collector auto-resolves GUID when unambiguous, but every segment retains both the labeled RRF slot and the resolved Fusion GUID/catalog fields.

### Live recording sessions {#live_recording}

One parent MQTT `session_id` owns the raw batch stream **and**, on finalize, the persisted NPZ for the whole run. Tool changes split that run into time segments for visuals and per-tool analysis. The collector maintains an explicit recording state machine and **at most one open logical tool segment**.

**Publisher rules (tap-testing / Duet):**

- Every `accel/batch` and `tool` event must carry the same `session_id` as `start_session`.
- Tool selection may arrive on `tap/{device}/tool` **or** `duet/{machine}/tool` when `session_id` aligns (Duet may also bind to the active non-stopped machine session).
- Prefer **`tap/#` for job enrichment**, then **`cam/#`**, then Duet: `job_file`, `tools_snapshot` / `tools`, status `recording`/`idle`, mode, sample rate. Duet job/state can fill gaps but must not overwrite richer tap/cam fields once set.
- Tool boundaries use ADXL-aligned `t_s` (not wall-clock `ts`). Missing `t_s` falls back to the latest received sample time.
- `start_session` / `end_session` may include `job_file` (G-code path) when a job is running. Optional `gcode_sha256` / `gcode_summary` from the publisher help resolve the CAM source.

**State machine:**

1. **`start` / `start_session`** → `recording_state=recording`. Open the initial segment **only when `tool_number` is known**; otherwise wait for the first `tool_selected`.
2. **Accel batches** buffer under the parent session and extend the open segment’s `t1` / sample count when a segment is open (no child recordings). Batches before any open segment still accumulate.
3. **`tool_offset_changed`** updates offset/snapshot provenance on the open segment **without** splitting.
4. **`tool_selected` / selection `change`** closes the prior segment at ADXL `t_s`, analyzes it, opens the next (Fusion GUID + feed/chipload stamped from the tool library when unambiguous).
5. **`stop` / `end_session`** closes the final segment, writes `{session_id}.npz` + `{session_id}.ui.json` (chart previews), runs whole-session finalization, sets `recording_state=stopped`.

Duplicate lifecycle/`event_id` tool events are idempotent. Batches after stop are accepted for recovery but flagged (`batch_after_stop`).

**Idle / fault finalize:**

- Mark `stalled` when no samples for `min(10, TAP_COLLECTOR_IDLE_TIMEOUT_S)` while still `recording`.
- Idle sweep (every ~5s) finalizes any open session whose `updated_at` is older than `TAP_COLLECTOR_IDLE_TIMEOUT_S` (**including zero-batch orphan starts**), writing JSON with warning `idle_timeout_empty` when no NPZ samples exist.
- Optional wall-clock cap: `TAP_COLLECTOR_MAX_SESSION_S` (>0) force-finalizes with warning `max_session_timeout`.
- Ops / SPA: `POST /api/v1/sessions/{id}/stop` force-finalizes; tap-testing also emits `end_session` after `MQTT_SESSION_IDLE_TIMEOUT_S` (default 25s) with no ADXL samples.

**RPM telemetry (live ArborCTL + leftover Modbus):**

- MQTT `tap/{device}/spindle` carries **decoded** ArborCTL samples (`source: arborctl`) with electrical `hz`, mechanical `rpm`, `poles`, optional `commanded_rpm` from RRF `spindles.active`, and load when H100 FC4 power is available. `t_s` is recording-relative (same origin as ADXL/tool events). Epoch-like `t_s` is rejected (`rpm_alignment=misaligned`).
- Identity: **`RPM = 120 × Hz / poles`** (2-pole 400 Hz → 24000 RPM). Collectors must **not** re-scale ArborCTL Hz/RPM with FluidNC `rpm*60/10` or treat `spindle_operating_frequency_hz × 60` as the live conversion (that convention is mechanical rev/s for analysis charts).
- H100 load comes from ArborCTL’s FC4 monitor (`h100Fc4Count=13` → `power_available: true` and `watts` / `load_percent`, including real idle `0`). Short clones (`h100Fc4Count=2`) omit load. Persist `load_samples` when `power_available` is true; the SPA overlays load % on the RPM timeline.
- MQTT `tap/{device}/modbus` remains for homing-GUI recordings. H100 `hr_513` / `ir_0` / `ir_1` are **always deci-Hz** (divide by 10; do not treat raw `< 1000` as already-Hz). Then `RPM = 120 × Hz / poles` (Milo default 2 poles).
- Rows are capped (`TAP_COLLECTOR_MODBUS_ROWS_MAX`, default 5000), normalized into `rpm_samples`, and persisted. Run ZIPs with `spindle-telemetry.jsonl` ingest the same way as live `/spindle`.
- **Observed evidence** for good/bad RPM rankings: **`arborctl`**, **`measured_modbus`**, and **`manual`**. Commanded Modbus (`hr_513`), commanded RRF (`spindles.active`), Fusion `start-values`, and RRF `tools[].spindleRpm` are **reference/contextual** only.
- **Transition windows** (`tool_change`, `spindle_start`, `spindle_stop`) exclude measured samples from observed rankings (default half-width `TAP_COLLECTOR_TRANSITION_HALF_WIDTH_S=0.75`); samples remain in `rpm_samples` for the timeline. Spindle edges use coils `co_73`/`co_74`/`co_75` when present, else commanded RPM crossing `TAP_COLLECTOR_SPINDLE_OFF_RPM`.
- Live segment reanalysis from RPM telemetry is throttled (`TAP_COLLECTOR_RPM_REANALYZE_MIN_INTERVAL_S`); finalize still recomputes authoritatively. Session / WebSocket payloads expose `rpm_samples` (bounded), `load_samples`, `transition_windows`, latest measured/commanded/load, and alignment status.

**Known limitations (this pass):**

- A single accel batch that straddles a tool boundary is attributed to whichever segment is open when the batch arrives (`n_samples`); the boundary time still uses event `t_s`.
- Full G-code bodies are never copied into the session store or shipped over MQTT — only a summary + CAM `source_ref`.

### CAM G-code source resolution {#cam_gcode}

On the RRF host, `gcodes -> /mnt/CAM/` is the same NFS content as fridge `192.168.86.221:/media/kad/data/CNC/CAM`. The gpu-rig-telemetry overlay mounts that share **read-only** at `/cam-gcode` (`TAP_COLLECTOR_CAM_GCODE_DIR`).

When a session has `job_file` (e.g. `0:/gcodes/part.gcode`):

1. Strip drive / leading `gcodes/` prefixes; look up by **basename** under the CAM share (Fusion posts under project folders; the machine path is flat under `gcodes/`).
2. Unique basename → resolve `source_ref` (share-relative path) and parse a compact `gcode_summary`.
3. Duplicate basenames → resolve only when `gcode_sha256` from the publisher matches exactly; otherwise mark `ambiguous` and list candidates (never guess by mtime).
4. Miss → accept a publisher `gcode_summary` with `source: rrf_download` when present.

Session detail exposes `job_file`, `source_ref`, `gcode_sha256`, `gcode_resolution_status`, and `gcode_summary` (tools used, tool-change byte offsets, spindle/feed command stats, G0/G1 counts). No UI path geometry in this pass.

### Future analytics roadmap {#future_analytics}

Not implemented yet — retained so the session schema stays forward-compatible:

- **G-code `S` timing:** align commanded spindle RPM from CAM / `file_position` with measured Modbus samples.
- **CAM simulation:** modal G-code interpreter (`G0`–`G3`, units, absolute/incremental, offsets), operation timelines via byte offsets + RRF `file_position`, cutting vs rapid vs dwell features, evidence-backed review flags. Advisory/read-only only — never send modified G-code to the machine.
- Machine-specific VFD nameplate fields beyond `poles` on ArborCTL / run-meta (H100 leftover Modbus defaults to 2-pole when nameplate is absent).

Historical sessions keep `job_file`, `source_ref`, `gcode_sha256`, command byte offsets, `file_position`, tool-segment bounds, raw Modbus rows, and `rpm_samples` so these can be added without re-recording.

### Tool segments and resonance analytics {#tool_segments}

Sessions are split into **tool segments** (time slices) so each ADXL window is analyzed with the correct flute count and tool metadata:

1. **Live** transitions from MQTT tool selection (above), or **auto-seed** from `tool_events` on import/finalize when no segments exist yet.
2. **Resolve** unambiguous Fusion `tool_number` → GUID (ambiguous matches are flagged for UI selection).
3. **Per-segment analysis**: natural frequency / peaks, predicted tooth-pass **avoid RPM bands** (with tolerance), suggested stable pocket (only when confidence is not `none`).
4. **Observed good/bad RPM** only when time-aligned **measured** RPM exists (`arborctl` from `tap/{device}/spindle` / `spindle-telemetry.jsonl`, `measured_modbus` from `modbus.csv` / MQTT, or **manual** reference RPM on the segment), excluding tool-change / spindle start-stop transition windows. Commanded Modbus, commanded RRF, Fusion `start-values`, and RRF `spindleRpm` are **reference/contextual** only — they drive the RPM timeline and theoretical risk markers, not observed ranking. Live H100 load % overlays the RPM plot when `power_available` is true.
5. **Cutting load estimate** from the selected Fusion tool’s feed / chipload preset (WoC 1.2–1.5 mm, DoC ≤ 4.5 mm).

#### Spindle frequency and Nyquist (Milo default)

Jarvis assumes a spindle operating frequency of **400 Hz → 24000 RPM** (`TAP_COLLECTOR_SPINDLE_FREQ_HZ`, aligned with tap-testing’s LDO Milo default). At the usual **800 S/s** ADXL rate, **Nyquist = 400 Hz**, so a dominant FFT peak near 400 Hz on continuous data is almost certainly **spindle 1× (or an alias)**, not a trustworthy tool-holder mode \(f_n\).

| Quantity | Value | Note |
|----------|------:|------|
| Spindle 1× at 24k RPM | 400 Hz | Indistinguishable from Nyquist at 800 S/s |
| Useful modal band | roughly &lt; ~350 Hz | Peaks near 400 Hz are spindle/alias suspects |
| Tooth pass at 24k, 4 flutes | 1600 Hz | Far above Nyquist — not directly observable |

Analysis therefore:

- Prefers the **highest-variance axis** (not magnitude) for modal ID.
- Labels / rejects peaks near the spindle line (`spindle_1x_suspect`).
- Records diagnostics: `axis_used`, `nyquist_hz`, `spindle_freq_hz`, `n_avoid_in_range`, `suggestion_confidence`, `warnings[]`.
- **Does not** treat “no in-range avoid RPMs” as “the whole 9k–24k band is good” — `suggested_rpm_*` is `null` with confidence `none`.
- Distinguishes **impact** (short / tap) vs **continuous** (`live_spindle` / `homing`) modes; continuous recordings are weaker evidence for modal \(f_n\).

Avoid centers still use \(\mathrm{RPM}_k = 60\,f_n/(N_t\,k)\). Bad zones usually mean a bad \(f_n\) (or flute count), not a wrong formula.

#### Cutting load estimate {#cutting_force}

When a segment has a Fusion tool with a cutting preset (`start-values.presets[]`: `v_f` / `f_z` / `n`, plus English aliases), the collector computes an engineering force envelope:

- Commanded feed = Fusion `v_f` (mm/min); effective chipload \(f_{z,\mathrm{eff}}=F/(N_t\cdot\mathrm{RPM})\); Fusion `f_z` kept as target with mismatch %.
- WoC sweep **1.2 / 1.35 / 1.5 mm**; DoC sweep **1.5 / 3.0 / 4.5 mm** (hard max 4.5 mm).
- Material coefficients default **6061 aluminum** (selectable 7075 / A36) — force-coefficient assumption only; feed/chipload are **never** invented from the material registry.
- Tangential force / torque / power / MRR mirror tap-testing’s engagement-scaled slotting model. **Not** an FRF or machine safety certification; 1.5 kW is a reference spindle power check only.

Missing Fusion feed → `cutting_force: null` with warning `missing_tool_cutting_preset`.

Interpretation limits: accelerometer response + tooth-pass formula is **not** a full stability-lobe diagram or a safety determination. Use predicted bands to narrow speeds; treat observed buckets as relative vibration evidence for that recording.

## HTTP API (port 8004)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health |
| `GET` | `/ui/` | Telemetry SPA (import + segments + MQTT stream + tool library) |
| `GET` | `/api/v1/sessions` | List sessions (memory + disk) |
| `POST` | `/api/v1/sessions/import` | Multipart CSV or ZIP run bundle (`homing.csv` preferred) |
| `GET` | `/api/v1/sessions/{id}` | Session detail + analysis + segments |
| `PATCH` | `/api/v1/sessions/{id}` | Update `notes` and/or `import_label` |
| `DELETE` | `/api/v1/sessions/{id}` | Delete JSON/NPZ/UI preview (`?force=true` if still recording) |
| `POST` | `/api/v1/sessions/{id}/stop` | Force-finalize a live session |
| `GET` | `/api/v1/sessions/{id}/series` | Downsampled time series (`max_points`, `regenerate=1` to rebuild cache) + segments |
| `GET` | `/api/v1/sessions/{id}/motion` | Downsampled XYZ(A) path for replay sim (`max_points`) |
| `POST` | `/api/v1/sessions/{id}/replay` | Rebuild `motion_samples` from durable telemetry log (bridge JSONL / Kafka) |

**Collector restart mid-job:** in-memory accel batches are lost, but **`telemetry/{session_id}/accel.jsonl`** (written by telemetry_bridge on the sessions PVC) keeps the full stream. On create/finalize the handler prefers that jsonl when it starts earlier or has more samples than RAM; startup also re-registers recently updated open sessions. Memory limit is **4Gi** for long 800 Hz jobs. Offline rebuild:

```bash
cd /media/kad/data/repositories/jarvis
.venv/bin/python scripts/rebuild_session_npz_from_jsonl.py --session-id SESSION_ID
```

Warning `rebuilt_from_accel_jsonl` is recorded on the session JSON.
| `GET` | `/api/v1/sessions/{id}/spectrum` | Magnitude FFT (`max_bins`, `regenerate=1` to rebuild cache) |
| `GET` | `/api/v1/sessions/{id}/live` | Rolling FFT / live stats + recording/tool/segment meta |
| `GET` | `/api/v1/sessions/{id}/segments` | Segments + segment analytics |
| `PUT` | `/api/v1/sessions/{id}/segments` | Replace segments and reanalyze (JSON body) |
| `POST` | `/api/v1/sessions/{id}/segments/reanalyze` | Recompute analytics for current segments |
| `WS` | `/api/v1/sessions/{id}/ws` | Live recording frames (~4 Hz): spectrum/series, `recording_state`, active tool/GUID/offsets/segment; sends a final stopped frame then closes |
| `GET` | `/api/v1/mqtt/events` | Query MQTT event stream (JSON-key `where`; scans persisted JSONL within retention) |
| `GET` | `/api/v1/mqtt/events/summary` | Buffer + persist stats / id range |
| `DELETE` | `/api/v1/mqtt/events` | Clear memory buffer and delete persisted day files |
| `GET` | `/api/v1/tools` | List Fusion tool catalog (`?q=` search) |
| `GET` | `/api/v1/tools/summary` | Catalog counts / last import |
| `GET` | `/api/v1/tools/lookup?number=N` | Tools with Fusion post-process number N |
| `GET` | `/api/v1/tools/{guid}` | Full tool (includes raw Fusion blob) |
| `PATCH` | `/api/v1/tools/{guid}` | Patch allowlisted summary fields (enforces unique tool number) |
| `DELETE` | `/api/v1/tools/{guid}` | Remove one catalog tool |
| `POST` | `/api/v1/tools/import` | Multipart Fusion `.tools` / `.json` / `.zip` (`replace=true` optional) |

Session list/detail also expose `recording_state` (`idle`/`recording`/`stalled`/`stopped`/`error`), `active_tool_*`, `active_segment_id`, `last_sample_at`, `notes`, and `warnings`. The SPA connects the selected session WebSocket for live badges/plots; that path is **not** the MQTT ring-buffer “Live” toggle.

### MQTT event stream {#mqtt_event_stream}

Every subscribed MQTT message is recorded into a **persisted** event stream on the session PVC (`$TAP_COLLECTOR_SESSION_DIR/mqtt_events/YYYY-MM-DD.jsonl`, UTC days). Accel arrays are summarized to lengths so files stay small. A hot in-memory window (default **5000**, `TAP_COLLECTOR_MQTT_EVENT_BUFFER`) is hydrated from disk on startup so the SPA survives collector restarts.

**Retention:** day files older than **`TAP_COLLECTOR_MQTT_EVENT_RETENTION_DAYS`** (default **30**) are deleted by a periodic prune (also on startup). `DELETE /api/v1/mqtt/events` clears memory **and** deletes JSONL day files (monotonic `next_id` is kept).

Open **MQTT stream** in the SPA or query:

```bash
# Tool changes for slot 3
curl -s 'http://tap.jarvis.lan/api/v1/mqtt/events?where_csv=event=change,tool_number=3'

# Dotted JSON keys + free text
curl -s 'http://tap.jarvis.lan/api/v1/mqtt/events?topic=duet/milo&where=job_file~part&q=gcode'

# Live tail after id 1200
curl -s 'http://tap.jarvis.lan/api/v1/mqtt/events?since_id=1200&limit=50'
```

**JSON-key `where` clauses** (repeat `where=` or pass comma-separated `where_csv=`):

| Clause | Meaning |
|--------|---------|
| `event=change` | Payload field equals (also works as `payload.event=change`) |
| `tool_number!=-1` | Not equal |
| `job_file~part` | Substring (case-insensitive) |
| `fn_hz>=100` | Numeric compare (`>`, `>=`, `<`, `<=`) |
| `session_id` | Key exists / is non-null |

Envelope fields `topic`, `id`, `ts`, `received_at`, `payload_kind` are queryable the same way. Buffer is process-local (lost on pod restart).

**Time fields (do not conflate):**

| Field | Where | Meaning |
|-------|-------|---------|
| `ts` / `received_at` | MQTT **envelope** | Collector receive time (Unix seconds, wall clock). `received_at` duplicates `ts` on new events. Values `< 1e9` are rejected at ingest. |
| `t_s` | Payload (tool/motion/spindle) | Recording-relative seconds since ADXL/session start — **not** a calendar date. |
| `t0_s` / `dt_s` | Payload (`accel/batch`) | Batch window start / step in recording seconds. |
| `ts` | Payload (optional) | Publisher wall clock when sent; may duplicate envelope time. Small values (`< 1e9`) are recording-relative in some test fixtures — the SPA labels these explicitly. |

The **MQTT stream** detail pane in the SPA shows **Received** (envelope wall clock) separately from **Recording t_s** / batch offsets (seconds, never formatted as calendar dates). Raw JSON remains available in a collapsible block for debugging.

#### `PUT /sessions/{id}/segments` body

```json
{
  "reanalyze": true,
  "segments": [
    {
      "segment_id": "seg-optional",
      "t0": 0.0,
      "t1": 12.5,
      "tool_number": 3,
      "tool_guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      "flutes": 4,
      "reference_rpm": 18000,
      "reference_rpm_source": "manual",
      "note": null
    }
  ]
}
```

Validation rejects overlaps, `t1 <= t0`, out-of-range times, unknown `tool_guid`, and invalid RPM. Manual `reference_rpm` without a source is stored as `manual` (observed evidence for that segment).

### Tool library (Fusion)

Export your library from Fusion Manufacture (`.tools` or JSON). Upload in the SPA (**Import tool library**) or:

```bash
curl -F "file=@MyLibrary.tools" http://tap.jarvis.lan/api/v1/tools/import
# Full replace (catalog becomes exactly the upload):
curl -F "file=@MyLibrary.tools" -F "replace=true" http://tap.jarvis.lan/api/v1/tools/import
```

- Upserts by Fusion **`guid`** (re-import updates existing tools).
- **Default import** also treats the upload as source of truth for **tool numbers**: when an imported tool shares `post-process.number` with a different GUID already in the catalog, the old entry is removed (`replaced_duplicates` / `removed` in the response).
- **`replace=true`** (form or query): catalog becomes solely the upload (tools not in the file are deleted; `mode: "replace"`).
- Manual `PATCH` / `DELETE` `/api/v1/tools/{guid}` for single-tool edits (PATCH also enforces unique tool numbers).
- Stored on the fridge NFS PVC under `tool_library/catalog.json` (+ `originals/` of each upload).
- Normalized fields: description, type, vendor, product_id, diameter (`geometry.DC`), `diameter_mm`, flutes (`NOF`), tool_number (from `post-process.number`), holder, unit, **`feed_mm_min`** / **`chipload_mm`** / **`reference_rpm`** from Fusion `start-values.presets[]` (`v_f`, `f_z`, `n` and English aliases; inch tools converted to mm) — RPM/feed are **estimated** library values, not measured.
- Live RRF `Tn` events and ZIP tool timelines resolve against this catalog via tool number / GUID mapping.


### Import contract

- **Primary**: `tap-testing/data/live_spindle/homing/<run>/homing.csv` — columns `t_s,ax_g,ay_g,az_g` plus optional `# sample_rate_hz,<hz>,,` row.
- **ZIP run bundle** (preferred for correlation): accel CSV plus optional sidecars by basename:
  - `tool-events.jsonl` — tool change timeline (`t_s` aligned with ADXL)
  - `tools-snapshot.json` — RRF tools table (`number`, `name`, `spindleRpm` as reference)
  - `run-meta.json` — session / job metadata
  - `modbus.csv` — leftover homing-GUI VFD registers; `hr_513` / `ir_0` are always deci-Hz (`RPM = 120 × (raw/10) / poles`)
  - `spindle-telemetry.jsonl` — ArborCTL live Hz/RPM/load (`source: arborctl`; load only when `power_available`)
- Standalone CSV imports remain supported (no auto segments until you add them in the UI).
- Form fields: `file` (required), `mode` (default `live_spindle`), `device_id` (default `import`).
- Persists `{session_id}.npz` + `{session_id}.json` + `{session_id}.ui.json` (downsampled series/spectrum for fast SPA loads) under `TAP_COLLECTOR_SESSION_DIR`, runs local/`tap_testing` analysis and per-segment analytics when segments exist.

**Cycle** CSVs (discrete taps) are secondary; the SPA is optimized for continuous live-spindle / homing runs.

### Smoke (historical import)

```bash
curl -F "file=@tap-testing/data/live_spindle/homing/20260411_125938/homing.csv" \
  -F "mode=live_spindle" \
  http://tap.jarvis.lan/api/v1/sessions/import
# open http://tap.jarvis.lan/ui/
```

## Dual analysis

- **Pi** publishes `source: "pi"` after local `analyze.py` / cycle analysis.
- **Collector** on session `stop` (or idle timeout) reassembles batches, runs analysis (`tap_testing.analyze_tap_data` when `PYTHONPATH` includes the sibling repo, else local FFT), stores `source: "jarvis"`, and optionally republishes to MQTT. Jarvis always passes `use_axis` (highest-variance), `spindle_operating_frequency_hz` (default 400), and spindle-aware suggestion guards even when the tap-testing engine is used.
- Sources are **not** overwritten when the other side publishes first.

## LLM forward (optional)

Set `TAP_COLLECTOR_LLM_FORWARD=true` and `JARVIS_LLM_BASE_URL=http://llm_api:8000/api/v1`. On analysis, the collector POSTs `event_type: data_analysis` with a **summary-only** body (never raw 800 Hz arrays) and an `Idempotency-Key`.

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `TAP_COLLECTOR_MQTT_HOST` | `mosquitto` | Broker host |
| `TAP_COLLECTOR_MQTT_PORT` | `1883` | Broker port |
| `TAP_COLLECTOR_MQTT_TOPICS` | `tap/#,duet/#,cam/#` | Comma-separated subscribe filters |
| `TAP_COLLECTOR_SESSION_DIR` | `./cache/tap_sessions` | Persisted session JSON + NPZ |
| `TAP_COLLECTOR_IDLE_TIMEOUT_S` | `30` | Finalize idle open sessions (including empty / zero-batch) |
| `TAP_COLLECTOR_MAX_SESSION_S` | `0` | Wall-clock max open session length; `0` disables |
| `TAP_COLLECTOR_LLM_FORWARD` | `false` | Forward summaries to LLM |
| `JARVIS_LLM_BASE_URL` | | LLM API `/api/v1` base |
| `TAP_COLLECTOR_FLUTES` | `4` | Default flute count for analysis |
| `TAP_COLLECTOR_SPINDLE_FREQ_HZ` | `400` | Milo spindle operating frequency (Hz); reference RPM = freq × 60 |
| `TAP_COLLECTOR_MAX_RPM` / `MIN_RPM` | `24000` / `9000` | RPM guidance bounds (`MAX_RPM` defaults to spindle_freq × 60 if unset) |
| `TAP_COLLECTOR_MQTT_EVENT_BUFFER` | `5000` | Hot in-memory MQTT event window (hydrated from disk on start) |
| `TAP_COLLECTOR_MQTT_EVENT_RETENTION_DAYS` | `30` | Delete persisted MQTT day files older than this many days |
| `TAP_COLLECTOR_MOTION_SAMPLES_MAX` | `10000` | Cap in-memory / persisted motion samples per session |
| `TAP_COLLECTOR_TELEMETRY_LOG_DIR` | (unset) | Shared NFS subdir written by **telemetry_bridge** for replay |
| `TAP_COLLECTOR_REPLAY_KAFKA_BOOTSTRAP` | (unset) | Optional Kafka/Redpanda bootstrap for replay PoC |
| `TAP_COLLECTOR_MODBUS_ROWS_MAX` | `5000` | Cap on persisted live Modbus rows per session |
| `TAP_COLLECTOR_RPM_SAMPLES_WS_MAX` | `500` | Recent `rpm_samples` window on live WebSocket frames |
| `TAP_COLLECTOR_RPM_REANALYZE_MIN_INTERVAL_S` | `1.0` | Throttle live segment reanalysis after Modbus |
| `TAP_COLLECTOR_TRANSITION_HALF_WIDTH_S` | `0.75` | Half-width of tool/spindle transition exclusion windows |
| `TAP_COLLECTOR_SPINDLE_OFF_RPM` | `50` | Commanded RPM below this counts as spindle off |

## Kubernetes

Optional overlay (not in the core LLM stack). Session data uses **fridge-prime NFS** (`nfs-storage`), not `emptyDir`:

| Resource | Notes |
|----------|--------|
| PV `nfs-fridge-tap-sessions` | `192.168.86.221:/media/kad/data/k8s/jarvis-llm/tap-sessions` |
| PVC `tap-collector-sessions` | 20Gi, `volumeName` bound |
| Mount | `/app/cache/tap_sessions` (session JSON + NPZ) |
| Memory | requests 512Mi / limits **4Gi** (long 800 Hz jobs) |
| PV `nfs-cam-gcode` | `192.168.86.221:/media/kad/data/CNC/CAM` (read-only; RRF `gcodes → /mnt/CAM/`) |
| PVC `cam-gcode` | `ReadOnlyMany`, `volumeName` bound |
| Mount | `/cam-gcode` (`TAP_COLLECTOR_CAM_GCODE_DIR`) |

```bash
# Preferred — purpose stack / single service (see docs/operations/deploy-stacks.md):
./deploy.sh --profile gpu-rig tap-collector
# or portable registry defaults:
./deploy.sh tap-collector

# Manual (gpu-rig):
# Operator — on fridge-prime, once:
mkdir -p /media/kad/data/k8s/jarvis-llm/tap-sessions

docker build -t 192.168.86.120:32000/jarvis_tap_collector:latest \
  -f tap_collector/Dockerfile ./tap_collector
docker push 192.168.86.120:32000/jarvis_tap_collector:latest
kubectl apply -k k8s/overlays/gpu-rig-telemetry/
kubectl -n jarvis-llm rollout restart deploy/tap-collector
kubectl -n jarvis-llm rollout status deploy/tap-collector
kubectl -n jarvis-llm get pvc tap-collector-sessions
kubectl -n jarvis-llm get svc mosquitto tap-collector
```

The LAN DNS overlay maps **`mqtt.jarvis.lan`** to Mosquitto's pinned MetalLB IP
(`192.168.86.7`). The collector is available at **`http://tap.jarvis.lan`**;
its LoadBalancer port **80** forwards to container port **8004**.

### Milo SBC (tap-testing publisher)

Pull/sync the sibling **tap-testing** tree that includes schema-v2 tool telemetry +
`event_type: start_session|end_session`, then restart the live spindle service:

```bash
# On Milo SBC — operator:
cd /path/to/tap-testing
git pull   # or rsync the updated package
pip install -e .   # if needed
export TAP_MQTT_HOST=mqtt.jarvis.lan
export TAP_MQTT_PORT=1883
export TAP_MQTT_DEVICE_ID=milo
export TAP_RRF_BASE=http://127.0.0.1
# optional: TAP_RRF_TOOL_TABLE_POLL_S=1.0
# restart your usual live_spindle / run_cycle unit or screen session
```

UI: `http://tap.jarvis.lan/ui/` — select a session to open the recording WebSocket
(recording badge, active T# / Fusion GUID, offsets, live plots). The MQTT ring
“Live” toggle remains a separate diagnostics stream.
