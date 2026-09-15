# Phase 0 — Optics bring-up (Pi + NoIR)

Universal Interactive Wands use a **retroreflective tip** + colocated **IR LEDs** + IR-sensitive camera. The tip appears as a bright blob.

## Hardware checklist

1. Spare Raspberry Pi + **Pi NoIR** (or IR-capable USB cam)
2. **940 nm IR LED** ring/array **next to the lens** (retroreflectors return light toward the source)
3. Official Interactive Wand **or** DIY tip (retroreflective tape / mocap dots)
4. Indoor range target: **0.5–2 m**

## Software check (no camera)

```bash
cd wand/edge/pi
pip install -r requirements.txt
python track_blob.py --synthetic --spell incendio --out /tmp/traj.jsonl
```

Expect JSON `lock_ok: true` and tip samples.

## Live camera

```bash
python track_blob.py --camera 0 --hud
```

Success criteria: stable tip lock for both official and DIY tips; trail draws cleanly on HUD.

## Station loop

With wand API up (`:8002`):

```bash
python station_client.py --synthetic --spell incendio --transcript incendio --effects
```
