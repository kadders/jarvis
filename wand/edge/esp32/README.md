# ESP32 compact station (Seeed XIAO ESP32-S3 Sense)

Phase 2–3 firmware: poll Jarvis `GET /api/v1/effects/last` and flash WS2812 on new `spell_id`.

```bash
cd wand/edge/esp32
pio run -t upload   # after setting WAND_WIFI_* and WAND_API_HOST in platformio.ini
```

Later: add SEN0158 I2C tip stream to `POST /api/v1/recognize` (same protocol as Pi).
