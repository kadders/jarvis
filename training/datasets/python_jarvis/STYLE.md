# Jarvis Python style guide (training corpus)

Format all Python with **Black** (88-character lines, `target-version = py310`).

## Black formatting

#### ✅ CORRECT - Black double quotes and trailing comma:
```python
def greet(name: str, *, excited: bool = False) -> str:
    message = f"Hello, {name}!"
    if excited:
        return message + "!"
    return message
```

#### ❌ INCORRECT - Single quotes and cramped layout:
```python
def greet(name:str,*,excited:bool=False)->str:
    message=f'Hello, {name}!'
    if excited: return message+'!'
    return message
```

#### ✅ CORRECT - Multiline call with trailing comma:
```python
resp = client.post(
    "/api/v1/events",
    json={"type": "query", "content": {"text": "hi"}},
    headers={"Idempotency-Key": key},
)
```

#### ❌ INCORRECT - One long line, no trailing comma:
```python
resp = client.post("/api/v1/events", json={"type": "query", "content": {"text": "hi"}}, headers={"Idempotency-Key": key})
```

## Type hints and future annotations

#### ✅ CORRECT - Future annotations + explicit types:
```python
from __future__ import annotations

from typing import Any


def load_config(path: str) -> dict[str, Any]:
    ...
```

#### ❌ INCORRECT - Missing future import, bare dict:
```python
def load_config(path):
    ...
```

## Exception handling

#### ✅ CORRECT - Specific exception:
```python
try:
    value = int(raw)
except ValueError:
    value = 0
```

#### ❌ INCORRECT - Bare except:
```python
try:
    value = int(raw)
except:
    value = 0
```

## FastAPI / Jarvis API patterns

#### ✅ CORRECT - Env-driven config with default:
```python
import os

FEEDBACK_DIR = os.environ.get("JARVIS_FEEDBACK_DIR", "cache/llm_feedback")
```

#### ❌ INCORRECT - Hardcoded absolute path:
```python
FEEDBACK_DIR = "/media/kad/data/k8s/jarvis-llm/llm-feedback"
```

#### ✅ CORRECT - Idempotency header on events:
```python
headers = {"Idempotency-Key": idempotency_key}
response = await client.post(f"{base}/events", json=body, headers=headers)
```

#### ❌ INCORRECT - Retry without idempotency key:
```python
response = await client.post(f"{base}/events", json=body)
```

## MQTT telemetry (tap-testing)

#### ✅ CORRECT - Optional MQTT from env, fail open:
```python
def mqtt_config_from_env() -> MqttTelemetryConfig | None:
    host = os.environ.get("TAP_MQTT_HOST", "").strip()
    if not host:
        return None
    return MqttTelemetryConfig(host=host)
```

#### ❌ INCORRECT - Always connect, block capture:
```python
def mqtt_config_from_env() -> MqttTelemetryConfig:
    return MqttTelemetryConfig(host="192.168.1.10")
```

## pytest conventions

#### ✅ CORRECT - Fixture with tmp_path:
```python
def test_writes_feedback(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.jsonl")
    store.append({"rating": 1, "prompt": "q", "response": "a"})
    assert store.count() == 1
```

#### ❌ INCORRECT - Writes to repo cache in tests:
```python
def test_writes_feedback():
    store = FeedbackStore("cache/llm_feedback/feedback.jsonl")
    store.append({"rating": 1})
```
