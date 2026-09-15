# Pi NoIR edge client

## Phase 0 — Optics

```bash
cd wand/edge/pi
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# No camera: synthetic IR tip path
python track_blob.py --synthetic --spell incendio --out /tmp/traj.jsonl
# Live NoIR (colocate 940 nm IR LEDs with lens)
python track_blob.py --camera 0 --hud
```

Success: `lock_ok: true` with tip points at 0.5–2 m for official or DIY retroreflective tips.

## Station client

```bash
# Wand API must be reachable (compose wand_service on :8002)
python station_client.py --synthetic --spell incendio --transcript incendio --effects
```

Streams trajectory to `POST /api/v1/recognize` and flashes mock/real LEDs on cast.

## Park-station loop (Phase 2–3)

```bash
# Ideal template paths (demo without camera)
python park_station.py --wand-url http://127.0.0.1:8002 --mode templates
# Optional: require spoken match
python park_station.py --with-transcript
```
