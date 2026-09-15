# Arduino / ESP32 style guide (Wand edge + tiny-ocr firmware)

## Non-blocking loop

#### ✅ CORRECT - millis() polling without blocking delay in loop:
```cpp
unsigned long lastPollMs = 0;
const unsigned long POLL_INTERVAL_MS = 500;

void loop() {
  if (millis() - lastPollMs >= POLL_INTERVAL_MS) {
    lastPollMs = millis();
    pollWandApi();
  }
  updateLeds();
}
```

#### ❌ INCORRECT - delay() blocks WiFi and LED updates:
```cpp
void loop() {
  pollWandApi();
  delay(500);
}
```

## WiFi reconnect

#### ✅ CORRECT - Reconnect state machine in loop:
```cpp
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastWifiAttemptMs > 5000) {
      lastWifiAttemptMs = millis();
      WiFi.reconnect();
    }
    return;
  }
  pollWandApi();
}
```

#### ❌ INCORRECT - Blocking reconnect spin:
```cpp
void loop() {
  while (WiFi.status() != WL_CONNECTED) {
    delay(1000);
  }
  pollWandApi();
}
```

## JSON parsing (ArduinoJson)

#### ✅ CORRECT - Sized JsonDocument and error check:
```cpp
JsonDocument doc;
DeserializationError err = deserializeJson(doc, body);
if (err) {
  return;
}
const char *spell = doc["spell_id"] | "";
```

#### ❌ INCORRECT - Undersized static doc, no error check:
```cpp
StaticJsonDocument<64> doc;
deserializeJson(doc, body);
String spell = doc["spell_id"];
```

## HTTP client

#### ✅ CORRECT - Always end HTTP connection:
```cpp
HTTPClient http;
http.begin(url);
int code = http.GET();
if (code == 200) {
  String body = http.getString();
  handleBody(body);
}
http.end();
```

#### ❌ INCORRECT - Missing http.end():
```cpp
HTTPClient http;
http.begin(url);
int code = http.GET();
if (code == 200) {
  handleBody(http.getString());
}
```

## PlatformIO build flags

#### ✅ CORRECT - Defaults with #ifndef overrides:
```cpp
#ifndef WAND_API_HOST
#define WAND_API_HOST "192.168.86.120"
#endif
#ifndef WAND_API_PORT
#define WAND_API_PORT 8002
#endif
```

#### ❌ INCORRECT - Hardcoded LAN IP with no override:
```cpp
#define WAND_API_HOST "192.168.86.120"
#define WAND_API_PORT 8002
```

## String building in hot path

#### ✅ CORRECT - snprintf into fixed buffer:
```cpp
char url[128];
snprintf(url, sizeof(url), "http://%s:%d/api/v1/effects/last", host, port);
http.begin(url);
```

#### ❌ INCORRECT - String concatenation allocates each poll:
```cpp
String url = String("http://") + host + ":" + String(port) + "/api/v1/effects/last";
http.begin(url);
```
