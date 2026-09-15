"""
Idempotency store for POST /api/v1/events (Idempotency-Key + replay within TTL).
"""
from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger


def canonical_body_hash(body: Dict[str, Any]) -> str:
    """Stable SHA-256 over sorted JSON for request body deduplication."""
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class IdempotencyRecord:
    body_hash: str
    event_id: str
    status: str
    response: Optional[Dict[str, Any]]
    error: Optional[str]


class IdempotencyStore(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[IdempotencyRecord]:
        pass

    @abstractmethod
    def set(
        self,
        key: str,
        body_hash: str,
        event_id: str,
        status: str,
        response: Optional[Dict[str, Any]],
        error: Optional[str],
        ttl_seconds: int,
    ) -> None:
        pass


class MemoryIdempotencyStore(IdempotencyStore):
    """LRU-style bounded store for single-worker / tests."""

    def __init__(self, max_entries: int = 10_000) -> None:
        self._max = max_entries
        self._data: Dict[str, tuple[float, IdempotencyRecord]] = {}

    def get(self, key: str) -> Optional[IdempotencyRecord]:
        now = time.time()
        item = self._data.get(key)
        if not item:
            return None
        exp, rec = item
        if exp < now:
            del self._data[key]
            return None
        return rec

    def set(
        self,
        key: str,
        body_hash: str,
        event_id: str,
        status: str,
        response: Optional[Dict[str, Any]],
        error: Optional[str],
        ttl_seconds: int,
    ) -> None:
        while len(self._data) >= self._max:
            # drop arbitrary oldest by expiry time
            oldest = min(self._data.items(), key=lambda kv: kv[1][0])
            del self._data[oldest[0]]
        deadline = time.time() + max(1, ttl_seconds)
        self._data[key] = (deadline, IdempotencyRecord(body_hash, event_id, status, response, error))


class RedisIdempotencyStore(IdempotencyStore):
    def __init__(self, url: str, key_prefix: str = "jarvis:idempotency:") -> None:
        import redis

        self._redis = redis.from_url(url, decode_responses=True)
        self._prefix = key_prefix

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> Optional[IdempotencyRecord]:
        raw = self._redis.get(self._k(key))
        if not raw:
            return None
        try:
            d = json.loads(raw)
            return IdempotencyRecord(
                body_hash=d["body_hash"],
                event_id=d["event_id"],
                status=d["status"],
                response=d.get("response"),
                error=d.get("error"),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Bad idempotency payload for {key}: {e}")
            return None

    def set(
        self,
        key: str,
        body_hash: str,
        event_id: str,
        status: str,
        response: Optional[Dict[str, Any]],
        error: Optional[str],
        ttl_seconds: int,
    ) -> None:
        payload = json.dumps(
            {
                "body_hash": body_hash,
                "event_id": event_id,
                "status": status,
                "response": response,
                "error": error,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self._redis.setex(self._k(key), max(1, ttl_seconds), payload)


def get_store(backend: str, redis_url: Optional[str], max_memory_entries: int, key_prefix: str) -> IdempotencyStore:
    b = (backend or "memory").lower()
    if b == "redis":
        if not redis_url:
            raise ValueError("REDIS_URL is required when IDEMPOTENCY_BACKEND=redis")
        return RedisIdempotencyStore(redis_url, key_prefix=key_prefix)
    return MemoryIdempotencyStore(max_entries=max_memory_entries)
