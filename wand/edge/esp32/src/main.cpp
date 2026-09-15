/**
 * Compact park-station edge for Seeed XIAO ESP32-S3 Sense.
 * Phase 2–3: WiFi poll last cast from Jarvis wand API + drive WS2812.
 * Optional: stream tip coords from SEN0158 IR pod (I2C) — stubbed.
 */
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <FastLED.h>

#ifndef WAND_WIFI_SSID
#define WAND_WIFI_SSID "YOUR_SSID"
#endif
#ifndef WAND_WIFI_PASS
#define WAND_WIFI_PASS "YOUR_PASS"
#endif
#ifndef WAND_API_HOST
#define WAND_API_HOST "192.168.86.120"
#endif
#ifndef WAND_API_PORT
#define WAND_API_PORT 8002
#endif

#define LED_PIN 1
#define NUM_LEDS 16
CRGB leds[NUM_LEDS];

String lastSpell;

void flashSpell(const String &spell) {
  CRGB c = CRGB::Green;
  if (spell == "incendio") c = CRGB::OrangeRed;
  else if (spell == "lumos") c = CRGB::White;
  else if (spell == "expelliarmus") c = CRGB::Red;
  else if (spell == "expecto_patronum") c = CRGB::Aqua;
  else if (spell == "alohomora") c = CRGB::Blue;
  fill_solid(leds, NUM_LEDS, c);
  FastLED.show();
  delay(500);
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  FastLED.show();
}

void setup() {
  Serial.begin(115200);
  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
  FastLED.clear(true);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WAND_WIFI_SSID, WAND_WIFI_PASS);
  Serial.print("WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.println(" ok");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    delay(1000);
    return;
  }
  HTTPClient http;
  String url = String("http://") + WAND_API_HOST + ":" + String(WAND_API_PORT) +
               "/api/v1/effects/last";
  http.begin(url);
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    JsonDocument doc;
    if (!deserializeJson(doc, body)) {
      const char *spell = doc["spell_id"] | "";
      if (spell[0] && lastSpell != spell) {
        lastSpell = spell;
        Serial.printf("cast %s\n", spell);
        flashSpell(lastSpell);
      }
    }
  }
  http.end();
  delay(500);
}
