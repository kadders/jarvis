# Oximeter collector (MQTT telemetry from ESP32 oximeters)

Ingests `oximeter/#` MQTT topics, records sessions, serves a web UI for live charts and config push.

## Run (Compose)

```bash
docker compose --profile telemetry up -d mosquitto oximeter_collector
curl -s http://localhost:8005/health
open http://localhost:8005/ui/
```

On LAN (k8s): **`http://oximeter.jarvis.lan/ui/`** (namespace **`health`**, MetalLB **192.168.86.12**).

## Device MQTT topics

| Topic | Direction | Payload |
|-------|-----------|---------|
| `oximeter/{device_id}/readings` | device → broker | `{"ts","hr","spo2","valid","finger","battery"}` |
| `oximeter/{device_id}/status` | device → broker (retained) | `{"online","fw","device_id","ip","rssi","battery",...}` |
| `oximeter/{device_id}/cmd/config` | broker → device | config JSON (same as device web POST) |
| `oximeter/{device_id}/config/ack` | device → broker | `{"ok":true}` or errors |
| `oximeter/{device_id}/cmd/reboot` | broker → device | reboot |

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ui/` | Web UI |
| GET | `/health` | Health |
| GET | `/api/v1/devices` | List devices |
| GET | `/api/v1/devices/{id}` | Device detail |
| GET | `/api/v1/devices/{id}/config` | Fetch config from device HTTP API |
| POST | `/api/v1/devices/{id}/config` | Push config via MQTT and/or HTTP |
| POST | `/api/v1/devices/{id}/reboot` | MQTT reboot command |
| GET | `/api/v1/config/template` | Default config template (env-driven) |
| GET | `/api/v1/sessions` | List sessions |
| GET | `/api/v1/sessions/{id}/series` | Downsampled HR/SpO2 points |
| GET | `/api/v1/sessions/{id}/analytics` | Min/max/mean HR and SpO2 |
| GET | `/api/v1/sessions/{id}/export.csv` | CSV export |
| WS | `/api/v1/devices/{id}/ws` | Live readings, status, config ack |
| GET | `/api/v1/mqtt/events` | MQTT event query (same as tap collector) |

### Config push example

```bash
curl -X POST http://oximeter.jarvis.lan/api/v1/devices/aabbccddeeff/config \
  -H 'Content-Type: application/json' \
  -d '{
    "v": 1,
    "via": "mqtt",
    "wifi": {"ssid": "MyNet", "pass": "secret"},
    "mqtt": {"enabled": true, "host": "mqtt.jarvis.lan", "port": 1883, "prefix": "oximeter/aabbccddeeff"},
    "ble": {"enabled": true, "name": "Oximeter-lab"},
    "sensor": {"led_current": 36}
  }'
```

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `OXI_COLLECTOR_PORT` | `8005` | HTTP port |
| `OXI_COLLECTOR_MQTT_HOST` | `mosquitto` | Broker |
| `OXI_COLLECTOR_MQTT_TOPICS` | `oximeter/#` | Subscribe filters |
| `OXI_COLLECTOR_STORE_DIR` | `./cache/oximeter` | Devices + sessions + mqtt_events |
| `OXI_COLLECTOR_IDLE_TIMEOUT_S` | `30` | Auto-close session after idle |
| `OXI_COLLECTOR_DEFAULT_WIFI_SSID` | `""` | Template / lab provisioning |
| `OXI_COLLECTOR_DEFAULT_WIFI_PASS` | `""` | Template Wi-Fi password |
| `OXI_COLLECTOR_DEFAULT_MQTT_HOST` | `mqtt.jarvis.lan` | Template MQTT host |

## Kubernetes

```bash
sudo mkdir -p /media/kad/data/k8s/health/oximeter-data   # on fridge-prime NFS host
docker build -t 192.168.86.120:32000/jarvis_oximeter_collector:latest -f oximeter_collector/Dockerfile ./oximeter_collector
docker push 192.168.86.120:32000/jarvis_oximeter_collector:latest
kubectl apply -k k8s/overlays/gpu-rig-health/
```

Deployed in namespace **`health`** (not `jarvis-llm`). MQTT broker remains in **`jarvis-llm`** (`mosquitto.jarvis-llm.svc.cluster.local`); devices still use **`mqtt.jarvis.lan:1883`**.

### Migration cleanup (jarvis-llm)

If oximeter was previously deployed under `jarvis-llm`, remove leftovers:

```bash
bash k8s/overlays/gpu-rig-health/cleanup-jarvis-llm-oximeter.sh
```

This deletes any stale deploy/service/configmap/PVC in `jarvis-llm`, the old PV `nfs-fridge-oximeter-data`, and host path `/media/kad/data/k8s/jarvis-llm/oximeter-data`.

## Firmware build defaults

Flash the oximeter with compile-time Wi‑Fi/MQTT defaults so it joins your network and publishes without SoftAP setup. See the Oximeter repo `firmware/oximeter/build_defaults.h.example`.

## Tests

```bash
cd oximeter_collector && pip install -r requirements.txt && pytest
```
