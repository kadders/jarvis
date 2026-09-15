# Jarvis tap-stream (session.mp4 + optional YouTube)

Cluster-side ffmpeg for tap-testing **remote** video mode: MJPEG from the Pi + MQTT overlay → fragmented **`session.mp4`** on shared NFS, with optional YouTube RTMP tee.

See sibling repo `tap-testing` → `docs/VIDEO_STREAMING.md`.

## Deploy

```bash
# Preferred (see docs/operations/deploy-stacks.md):
./deploy.sh tap-stream
./deploy.sh --profile gpu-rig tap-stream   # lab NFS / MetalLB overlay

# Manual:
docker build -t 192.168.86.120:32000/jarvis_tap_stream:latest -f tap_stream/Dockerfile ./tap_stream
docker push 192.168.86.120:32000/jarvis_tap_stream:latest
kubectl apply -k k8s/overlays/gpu-rig-telemetry/
```

Health: `GET /health` on port 8005 (LoadBalancer `192.168.86.11` / `stream.jarvis.lan`).

Fields include `recordings_dir`, `output_mp4`, `youtube_configured`, `ffmpeg_running`, `ffmpeg_restarts`, `ffmpeg_parts`.

## Storage

Overlay `nfs-tap-recordings` mounts fridge `192.168.86.221:/media/kad/data/videos/milo` at **`/mnt/recordings`** (same path as Milo `TAP_VIDEO_DIR`). Each session writes:

`{TAP_STREAM_RECORDINGS_DIR}/{session_id}/session.mp4`

A session starts when **either** recordings dir **or** YouTube stream key is configured.

Encode writes staging parts under the pod work dir (`session_part001.mp4`, …), then concats/promotes onto NFS at clean session stop (avoids NFS latency back-pressuring MJPEG ingest). If ffmpeg dies unexpectedly (e.g. exit **-7 / SIGBUS**), stderr is drained, a partial part is kept, and ffmpeg is **restarted** for the same `session_id` (capped retries; health exposes `ffmpeg_restarts` / `ffmpeg_parts`).

## Config

| Env / ConfigMap key | Purpose |
|-----|---------|
| `TAP_STREAM_DEVICE_ID` | Match Pi `TAP_MQTT_DEVICE_ID` |
| `TAP_STREAM_MJPEG_URL` | Pi ustreamer LAN URL |
| `TAP_STREAM_RECORDINGS_DIR` | NFS mount for `session.mp4` (default `/mnt/recordings`) |
| `TAP_YOUTUBE_ENABLED` | ConfigMap — when `false`, MP4-only (stream key in Secret ignored) |
| `TAP_YOUTUBE_STREAM_KEY` | Secret — optional YouTube ingest (used only if enabled) |
| `TAP_VIDEO_ROTATE` / `TAP_VIDEO_FPS` / `TAP_VIDEO_BITRATE` | Encode profile (align with Pi ustreamer FPS) |
| `TAP_STREAM_CONFIG_POLL_S` | Seconds between ConfigMap/Secret file polls (default 15) |

ConfigMap and Secret are **mounted as files** under `/etc/tap-stream/{config,secrets}` and polled. Edit the ConfigMap to change FPS / YouTube enable without restarting the pod; new values apply on the **next session**.

Health: `GET /health` includes `youtube_enabled`, `youtube_configured`, `fps`, `config_fingerprint`.

MQTT: subscribes to `tap/{device}/session`, `video_overlay`, `accel/batch`.

**Does not** subscribe to `tap/…/motion` — structured coordinates are for **tap_collector** replay/sim ([motion topic](tap-collector.md#motion_topic)), not ffmpeg overlay.

### Failure modes

- **SIGBUS / exit -7:** usually decoder/writer pressure at 30 fps. Parts already written are kept; restarts continue the session. Prefer spacious local `TAP_STREAM_WORK_DIR`; fall back to `TAP_VIDEO_FPS=15` via ConfigMap if needed.
- **Partial MP4 without restart (older builds):** middle of the job is not recoverable; keep the short file and rely on Pi CSV / collector `accel.jsonl` for telemetry.

## Related

- [Tap collector — motion](tap-collector.md#motion_topic)
- tap-testing `docs/MQTT_PAYLOAD_REFERENCE.md` (motion + video_overlay)
