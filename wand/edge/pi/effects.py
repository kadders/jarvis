"""Local effect drivers for Pi (WS2812 / relay) with safe mock fallback."""

from __future__ import annotations

import json
import time
from typing import Any


class EffectController:
    def __init__(self, enabled: bool = False, pin: int = 18, relay_pin: int = 23):
        self.enabled = enabled
        self.pin = pin
        self.relay_pin = relay_pin
        self._pixels = None
        self._gpio = None
        if enabled:
            self._try_init_hardware()

    def _try_init_hardware(self) -> None:
        try:
            import board
            import neopixel

            self._pixels = neopixel.NeoPixel(board.D18, 30, auto_write=True)
        except Exception:
            self._pixels = None
        try:
            import RPi.GPIO as GPIO

            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.relay_pin, GPIO.OUT)
            GPIO.output(self.relay_pin, GPIO.LOW)
            self._gpio = GPIO
        except Exception:
            self._gpio = None

    def play(self, spell_id: str, score: float = 1.0) -> dict[str, Any]:
        event = {
            "event": "effect",
            "spell_id": spell_id,
            "score": score,
            "ts": time.time(),
            "hardware": bool(self._pixels or self._gpio),
        }
        colors = {
            "incendio": (255, 80, 0),
            "lumos": (255, 255, 220),
            "alohomora": (80, 180, 255),
            "expecto_patronum": (200, 220, 255),
            "expelliarmus": (255, 40, 40),
            "revelio": (180, 80, 255),
            "wingardium_leviosa": (120, 255, 160),
        }
        rgb = colors.get(spell_id, (0, 255, 120))
        if self._pixels is not None:
            self._pixels.fill(rgb)
            time.sleep(0.4)
            self._pixels.fill((0, 0, 0))
        if self._gpio is not None and spell_id in ("alohomora", "incendio"):
            self._gpio.output(self.relay_pin, self._gpio.HIGH)
            time.sleep(0.2)
            self._gpio.output(self.relay_pin, self._gpio.LOW)
        print(json.dumps(event), flush=True)
        return event

    def shutdown(self) -> None:
        if self._pixels is not None:
            self._pixels.fill((0, 0, 0))
        if self._gpio is not None:
            try:
                self._gpio.cleanup()
            except Exception:
                pass
