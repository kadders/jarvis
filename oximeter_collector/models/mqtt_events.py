"""MQTT event ring buffer with optional NFS-backed JSONL persistence.

Hot path keeps a bounded in-memory deque for the SPA. When ``store_dir`` is set,
every summarized event is appended to daily ``YYYY-MM-DD.jsonl`` files under that
directory (typically the session PVC). Files older than ``retention_days`` are
pruned. On startup the newest ``max_events`` are reloaded so the UI survives
collector restarts.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

# Large array fields in accel batches — store lengths only.
_ARRAY_KEYS = frozenset({"ax", "ay", "az", "samples", "data"})

_CLAUSE_RE = re.compile(
    r"^(?P<key>[A-Za-z0-9_.\[\]]+)\s*(?P<op>=~|!=|>=|<=|=|~|>|<)\s*(?P<val>.*)$"
)

_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")


def get_by_path(obj: Any, path: str) -> Any:
    """
    Resolve a dotted / bracket JSON key path.

    Examples:
      ``event`` → payload.event when used against envelope+payload merge
      ``payload.tool_number``
      ``ax[0]`` (rare; usually arrays are summarized)
    """
    if not path:
        return None
    cur: Any = obj
    # Split on dots but keep bracket segments: a.b[0].c
    parts: list[str] = []
    buf = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if buf:
                parts.append(buf)
                buf = ""
            i += 1
            continue
        if ch == "[":
            if buf:
                parts.append(buf)
                buf = ""
            j = path.find("]", i)
            if j < 0:
                return None
            parts.append(path[i : j + 1])
            i = j + 1
            continue
        buf += ch
        i += 1
    if buf:
        parts.append(buf)

    for part in parts:
        if cur is None:
            return None
        if part.startswith("[") and part.endswith("]"):
            idx_s = part[1:-1]
            try:
                idx = int(idx_s)
            except ValueError:
                return None
            if not isinstance(cur, (list, tuple)) or idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
            continue
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    return cur


def summarize_payload(data: Any) -> Any:
    """Deep-copy-ish summarize: replace large numeric arrays with length metadata."""
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            if k in _ARRAY_KEYS and isinstance(v, list):
                out[k] = {"_type": "array", "n": len(v)}
                if v:
                    try:
                        out[k]["sample0"] = v[0]
                    except Exception:
                        pass
            else:
                out[k] = summarize_payload(v)
        return out
    if isinstance(data, list):
        if len(data) > 32 and all(isinstance(x, (int, float)) for x in data[:8]):
            return {"_type": "array", "n": len(data)}
        return [summarize_payload(x) for x in data[:64]]
    return data


def parse_where_clause(clause: str) -> tuple[str, str, str] | None:
    """Parse ``key=value`` / ``key!=x`` / ``key~substr`` / numeric compares."""
    clause = (clause or "").strip()
    if not clause:
        return None
    m = _CLAUSE_RE.match(clause)
    if not m:
        # bare key → exists / truthy
        if re.match(r"^[A-Za-z0-9_.\[\]]+$", clause):
            return clause, "exists", ""
        return None
    return m.group("key"), m.group("op"), m.group("val").strip()


def _coerce_compare(left: Any, right: str) -> tuple[Any, Any]:
    """Best-effort type coercion for comparisons."""
    if left is None:
        return None, right
    if isinstance(left, bool):
        rl = right.lower()
        if rl in ("true", "1", "yes"):
            return left, True
        if rl in ("false", "0", "no"):
            return left, False
    if isinstance(left, (int, float)) and not isinstance(left, bool):
        try:
            if "." in right:
                return float(left), float(right)
            return float(left), float(right)
        except ValueError:
            return left, right
    # try numeric if both look numeric
    try:
        return float(left), float(right)
    except (TypeError, ValueError):
        return left, right


def match_clause(doc: dict[str, Any], key: str, op: str, val: str) -> bool:
    """Evaluate one where-clause against an event document (envelope + flat payload keys)."""
    # Search envelope first, then payload, then merged view for bare keys
    raw = get_by_path(doc, key)
    if raw is None and not key.startswith("payload.") and not key.startswith("topic"):
        raw = get_by_path(doc.get("payload"), key)
        if raw is None:
            raw = get_by_path(doc, f"payload.{key}")

    if op == "exists":
        return raw is not None

    if op in ("=", "=="):
        if raw is None:
            return False
        left, right = _coerce_compare(raw, val)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) == float(right)
        return str(left) == str(right)

    if op == "!=":
        if raw is None:
            return True
        left, right = _coerce_compare(raw, val)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return float(left) != float(right)
        return str(left) != str(right)

    if op in ("~", "=~"):
        if raw is None:
            return False
        return val.lower() in str(raw).lower()

    if op in (">", ">=", "<", "<="):
        if raw is None:
            return False
        left, right = _coerce_compare(raw, val)
        try:
            lf, rf = float(left), float(right)
        except (TypeError, ValueError):
            return False
        if op == ">":
            return lf > rf
        if op == ">=":
            return lf >= rf
        if op == "<":
            return lf < rf
        return lf <= rf

    return False


def match_event(
    event: dict[str, Any],
    *,
    topic: str | None = None,
    q: str | None = None,
    where: list[str] | None = None,
) -> bool:
    if topic:
        t = str(topic).strip()
        if t and t not in str(event.get("topic") or ""):
            return False
    clauses = where or []
    for clause in clauses:
        parsed = parse_where_clause(clause)
        if parsed is None:
            continue
        key, op, val = parsed
        if not match_clause(event, key, op, val):
            return False
    if q:
        ql = q.lower()
        blob = f"{event.get('topic', '')} {event.get('raw_preview', '')}"
        try:
            blob += " " + json.dumps(event.get("payload"), default=str)
        except Exception:
            blob += " " + str(event.get("payload"))
        if ql not in blob.lower():
            return False
    return True


class MqttEventStore:
    """Bounded memory ring + optional daily JSONL persistence with retention."""

    def __init__(
        self,
        max_events: int = 5000,
        *,
        store_dir: Path | str | None = None,
        retention_days: int = 30,
    ) -> None:
        self.max_events = max(100, int(max_events))
        self.retention_days = max(0, int(retention_days))
        self.store_dir = Path(store_dir) if store_dir else None
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._seq = 0
        self._lock = threading.Lock()
        self._last_prune_mono = 0.0
        self._persisted_approx = 0
        if self.store_dir is not None:
            try:
                self.store_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.warning("MQTT event store dir {}: {}", self.store_dir, e)
                self.store_dir = None
        if self.store_dir is not None:
            self._load_meta()
            self.prune(force=True)
            self._hydrate_from_disk()

    @property
    def meta_path(self) -> Path | None:
        if self.store_dir is None:
            return None
        return self.store_dir / "_meta.json"

    def _load_meta(self) -> None:
        path = self.meta_path
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._seq = max(0, int(data.get("next_id") or 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning("Failed to load MQTT event meta: {}", e)

    def _save_meta(self) -> None:
        path = self.meta_path
        if path is None:
            return
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps({"next_id": self._seq, "updated_at": time.time()}, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as e:
            logger.warning("Failed to save MQTT event meta: {}", e)

    def _day_path(self, ts: float) -> Path:
        assert self.store_dir is not None
        day = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        return self.store_dir / f"{day}.jsonl"

    def _list_day_files(self) -> list[Path]:
        if self.store_dir is None or not self.store_dir.is_dir():
            return []
        files = [
            p
            for p in self.store_dir.iterdir()
            if p.is_file() and _DAY_RE.match(p.name)
        ]
        return sorted(files, key=lambda p: p.name)

    def _cutoff_day(self) -> str | None:
        if self.retention_days <= 0:
            return None
        cutoff = datetime.now(tz=timezone.utc).date() - timedelta(days=self.retention_days)
        return cutoff.isoformat()

    def prune(self, *, force: bool = False) -> dict[str, Any]:
        """Delete day files older than retention; no-op if retention disabled."""
        with self._lock:
            return self._prune_locked(force=force)

    def _prune_locked(self, *, force: bool = False) -> dict[str, Any]:
        now_m = time.monotonic()
        if not force and (now_m - self._last_prune_mono) < 300.0:
            return {"pruned": 0, "skipped": True}
        self._last_prune_mono = now_m
        if self.store_dir is None or self.retention_days <= 0:
            return {"pruned": 0, "retention_days": self.retention_days}
        cutoff = self._cutoff_day()
        pruned = 0
        for path in self._list_day_files():
            m = _DAY_RE.match(path.name)
            if not m:
                continue
            if cutoff and m.group(1) < cutoff:
                try:
                    path.unlink()
                    pruned += 1
                except OSError as e:
                    logger.warning("Failed to prune {}: {}", path, e)
        if pruned:
            logger.info(
                "Pruned {} MQTT event day file(s) older than {}d",
                pruned,
                self.retention_days,
            )
            self._persisted_approx = self._count_disk_lines()
        return {
            "pruned": pruned,
            "retention_days": self.retention_days,
            "cutoff_day": cutoff,
        }

    def maybe_prune(self) -> None:
        """Cheap periodic prune hook (idle sweep)."""
        try:
            self.prune(force=False)
        except Exception as e:
            logger.debug("MQTT event prune failed: {}", e)

    def _append_disk(self, event: dict[str, Any]) -> None:
        if self.store_dir is None:
            return
        path = self._day_path(float(event["ts"]))
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, separators=(",", ":"), default=str))
                f.write("\n")
            self._save_meta()
            self._persisted_approx += 1
        except OSError as e:
            logger.warning("Failed to persist MQTT event: {}", e)

    def _hydrate_from_disk(self) -> None:
        """Load newest events into the memory ring (up to max_events)."""
        if self.store_dir is None:
            return
        collected: list[dict[str, Any]] = []
        max_id = self._seq
        for path in reversed(self._list_day_files()):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as e:
                logger.warning("Failed to read {}: {}", path, e)
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict) or "id" not in ev:
                    continue
                try:
                    eid = int(ev["id"])
                except (TypeError, ValueError):
                    continue
                max_id = max(max_id, eid)
                collected.append(ev)
                if len(collected) >= self.max_events:
                    break
            if len(collected) >= self.max_events:
                break
        collected.reverse()  # oldest → newest for deque append
        self._events.clear()
        for ev in collected:
            self._events.append(ev)
        self._seq = max(self._seq, max_id)
        self._persisted_approx = self._count_disk_lines()
        if collected:
            logger.info(
                "Hydrated {} MQTT events from disk (next_id={})",
                len(collected),
                self._seq,
            )

    def _count_disk_lines(self) -> int:
        n = 0
        for path in self._list_day_files():
            try:
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.strip():
                            n += 1
            except OSError:
                continue
        return n

    def _iter_disk_newest(self) -> Iterator[dict[str, Any]]:
        for path in reversed(self._list_day_files()):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict) and "id" in ev:
                    yield ev

    def record(
        self,
        topic: str,
        payload: bytes | str,
        *,
        ts: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            raw_bytes = (
                payload
                if isinstance(payload, (bytes, bytearray))
                else str(payload).encode("utf-8", errors="replace")
            )
            text = (
                payload
                if isinstance(payload, str)
                else bytes(payload).decode("utf-8", errors="replace")
            )
            parsed: Any
            kind = "text"
            try:
                parsed = json.loads(text)
                kind = "json"
            except Exception:
                parsed = text[:2000]
                kind = "text"

            summary = summarize_payload(parsed) if kind == "json" else parsed
            now = time.time()
            received = float(ts if ts is not None else now)
            if received < 1e9:
                received = now
            event = {
                "id": self._seq,
                "ts": received,
                "received_at": received,
                "topic": topic,
                "payload_kind": kind,
                "bytes": len(raw_bytes),
                "payload": summary,
                "raw_preview": text[:240] if kind == "text" else None,
            }
            self._events.append(event)
            self._append_disk(event)
            return event

    def query(
        self,
        *,
        topic: str | None = None,
        q: str | None = None,
        where: list[str] | None = None,
        since_id: int | None = None,
        since_ts: float | None = None,
        limit: int = 100,
        order: str = "desc",
        from_disk: bool = True,
    ) -> dict[str, Any]:
        limit = max(1, min(1000, int(limit)))
        matched: list[dict[str, Any]] = []
        with self._lock:
            # Prefer disk scan when persistence is enabled so queries span retention,
            # not only the hot memory window. Fall back to memory-only otherwise.
            if from_disk and self.store_dir is not None:
                items_iter: Iterator[dict[str, Any]] = self._iter_disk_newest()
                if order == "asc":
                    # Ascending: collect then reverse at end is awkward for limit;
                    # scan oldest-first from day files.
                    items_iter = self._iter_disk_oldest()
            else:
                items = list(self._events)
                if order != "asc":
                    items = list(reversed(items))
                items_iter = iter(items)

            for ev in items_iter:
                if since_id is not None and int(ev["id"]) <= int(since_id):
                    continue
                if since_ts is not None and float(ev["ts"]) < float(since_ts):
                    continue
                if not match_event(ev, topic=topic, q=q, where=where):
                    continue
                matched.append(ev)
                if len(matched) >= limit:
                    break

            n_mem = len(self._events)
            n_disk = self._persisted_approx if self.store_dir is not None else n_mem
            next_id = self._seq

        return {
            "n_total": n_disk if self.store_dir is not None else n_mem,
            "n_memory": n_mem,
            "n_returned": len(matched),
            "max_events": self.max_events,
            "retention_days": self.retention_days,
            "persist_dir": str(self.store_dir) if self.store_dir else None,
            "next_id": next_id,
            "events": matched,
            "query": {
                "topic": topic,
                "q": q,
                "where": where or [],
                "since_id": since_id,
                "since_ts": since_ts,
                "limit": limit,
                "order": order,
                "from_disk": from_disk and self.store_dir is not None,
            },
        }

    def _iter_disk_oldest(self) -> Iterator[dict[str, Any]]:
        for path in self._list_day_files():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict) and "id" in ev:
                    yield ev

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            removed = 0
            if self.store_dir is not None and self.store_dir.is_dir():
                # Remove every day file (and any stray *.jsonl), not only regex matches.
                for path in list(self.store_dir.glob("*.jsonl")):
                    try:
                        path.unlink()
                        removed += 1
                    except OSError as e:
                        logger.warning("Failed to clear {}: {}", path, e)
                # Keep next_id monotonic across clear so live tails do not rewind.
                self._save_meta()
                self._persisted_approx = self._count_disk_lines()
                if self._persisted_approx:
                    logger.warning(
                        "MQTT clear left {} persisted line(s) after removing {} file(s)",
                        self._persisted_approx,
                        removed,
                    )
                else:
                    logger.info("MQTT event stream cleared ({} day file(s) removed)", removed)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            oldest = self._events[0]["id"] if self._events else None
            newest = self._events[-1]["id"] if self._events else None
            return {
                "n_events": len(self._events),
                "n_persisted": self._persisted_approx if self.store_dir else len(self._events),
                "max_events": self.max_events,
                "retention_days": self.retention_days,
                "persist_dir": str(self.store_dir) if self.store_dir else None,
                "next_id": self._seq,
                "oldest_id": oldest,
                "newest_id": newest,
            }
