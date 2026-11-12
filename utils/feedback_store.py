"""Append-only feedback / preference store for LLM learning."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _tokenize_text(text: str) -> set[str]:
    return {
        t
        for t in "".join(c.lower() if c.isalnum() else " " for c in text).split()
        if len(t) > 2
    }


def is_ask_local_record(rec: Dict[str, Any]) -> bool:
    """Heuristic: MCP ask_local / Query: style prompts (model answers)."""
    prompt = str(rec.get("prompt") or "")
    pl = prompt.lower()
    if "jarvis_ask_local" in pl:
        return True
    if prompt.startswith("Query:") or "\nQuery:" in prompt:
        return True
    if pl.startswith("ask local") or "ask local jarvis" in pl:
        return True
    return False


def is_outcome_record(rec: Dict[str, Any]) -> bool:
    meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
    return meta.get("kind") == "outcome"


VALID_DOMAINS = frozenset(
    {"python_jarvis", "arduino_wand", "rrf_nxt", "cad", "mentra", "general"}
)


def record_domain(rec: Dict[str, Any]) -> Optional[str]:
    meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
    domain = meta.get("domain")
    if domain and str(domain) in VALID_DOMAINS:
        return str(domain)
    return None


def infer_domain_from_text(text: str) -> Optional[str]:
    """Heuristic domain tag from prompt/response/call text."""
    t = text.lower()
    if any(
        k in t
        for k in (".cpp", "esp32", "platformio", "arduino", "fastled", "wifimode")
    ):
        return "arduino_wand"
    if any(
        k in t
        for k in (
            "mentra",
            "mentraglass",
            "@mentra/miniapp",
            "@mentra/bluetooth-sdk",
            "registerminiapp",
            "devicemodels.mentralive",
            "mentra live",
            "mentraos",
            "session.display.render",
            "create-mentra-miniapp",
        )
    ):
        return "mentra"
    if any(
        k in t
        for k in (
            "fusion 360",
            "fusion360",
            "create_box",
            "get_scene_info",
            "chipload",
            "feed_mm_min",
            "post-process.number",
            "manufacture",
            "toolpath",
            "adaptive clearing",
            "cutting_force",
            "fusion mcp",
        )
    ):
        return "cad"
    if any(
        k in t
        for k in (
            "reprapfirmware",
            "rrf",
            "nxt",
            "gcode",
            ".g\n",
            "macro",
            "inputs[state.thisinput]",
        )
    ):
        return "rrf_nxt"
    if any(
        k in t
        for k in (
            ".py",
            "pytest",
            "fastapi",
            "black",
            "python",
            "mqtt",
            "jarvis",
            "pydantic",
        )
    ):
        return "python_jarvis"
    return None


def slice_tags_for(rec: Dict[str, Any]) -> List[str]:
    tags: List[str] = ["all"]
    src = str(rec.get("source") or "other")
    tags.append(src)
    ask = is_ask_local_record(rec)
    outcome = is_outcome_record(rec)
    if ask:
        tags.append("ask_local")
    if outcome and not ask:
        tags.append("agent_outcome")
    domain = record_domain(rec)
    if domain:
        tags.append(domain)
    return tags


def _rating_bucket(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    c: Counter = Counter()
    for r in rows:
        try:
            c[int(r.get("rating", 0))] += 1
        except (TypeError, ValueError):
            c[0] += 1
    pos = int(c.get(1, 0))
    neg = int(c.get(-1, 0))
    zero = int(c.get(0, 0))
    judged = pos + neg
    return {
        "n": len(rows),
        "pos": pos,
        "neg": neg,
        "zero": zero,
        "judged": judged,
        "accuracy": (pos / judged) if judged else None,
    }


def _is_exportable_pair(rec: Dict[str, Any]) -> bool:
    prompt = (rec.get("prompt") or "").strip()
    if not prompt:
        return False
    chosen = (
        rec.get("preferred") or rec.get("correction") or rec.get("chosen") or ""
    ).strip()
    rejected = (rec.get("rejected") or "").strip()
    if not rejected and rec.get("rating") is not None:
        try:
            if int(rec["rating"]) < 0:
                rejected = (rec.get("response") or "").strip()
        except (TypeError, ValueError):
            pass
    if not chosen or not rejected:
        return False
    return chosen != rejected


def _preview(text: Any, limit: int = 240) -> str:
    s = str(text or "")
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


class FeedbackStore:
    """JSONL feedback store with simple keyword retrieval for few-shot injection."""

    def __init__(self, path: str, *, max_inject: int = 3):
        self.path = Path(path)
        self.max_inject = max(0, max_inject)
        self._lock = threading.Lock()
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._records: List[Dict[str, Any]] = []
        self._mtime: float = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        self._by_id.clear()
        self._records.clear()
        if not self.path.is_file():
            self._mtime = 0.0
            return
        try:
            self._mtime = self.path.stat().st_mtime
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._index(rec)
        except OSError:
            self._mtime = 0.0

    def reload_if_stale(self) -> bool:
        """Re-read JSONL when the file mtime changes (MCP / UI writes)."""
        try:
            mtime = self.path.stat().st_mtime if self.path.is_file() else 0.0
        except OSError:
            return False
        with self._lock:
            if mtime == self._mtime:
                return False
            self._load()
            return True

    def _index(self, rec: Dict[str, Any]) -> None:
        fid = rec.get("feedback_id")
        if fid:
            self._by_id[str(fid)] = rec
        self._records.append(rec)

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        rec = dict(record)
        rec.setdefault("feedback_id", str(uuid.uuid4()))
        rec.setdefault("created_at", time.time())
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                self._mtime = self.path.stat().st_mtime
            except OSError:
                pass
            self._index(rec)
        return rec

    def get(self, feedback_id: str) -> Optional[Dict[str, Any]]:
        self.reload_if_stale()
        with self._lock:
            rec = self._by_id.get(feedback_id)
            return dict(rec) if rec else None

    def iter_records(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._records)

    def count(self) -> int:
        self.reload_if_stale()
        with self._lock:
            return len(self._records)

    def summary(self, *, day_limit: int = 30) -> Dict[str, Any]:
        self.reload_if_stale()
        with self._lock:
            rows = list(self._records)

        by_rating: Counter = Counter()
        by_source: Counter = Counter()
        by_kind: Counter = Counter()
        ask_local: List[Dict[str, Any]] = []
        agent_outcome: List[Dict[str, Any]] = []
        mcp_rows: List[Dict[str, Any]] = []
        ui_rows: List[Dict[str, Any]] = []
        domain_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        with_correction = 0
        exportable = 0
        by_day: Dict[str, Counter] = defaultdict(Counter)

        for r in rows:
            try:
                rating = int(r.get("rating", 0))
            except (TypeError, ValueError):
                rating = 0
            by_rating[rating] += 1
            src = str(r.get("source") or "other")
            by_source[src] += 1
            meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            kind = meta.get("kind")
            by_kind[str(kind) if kind is not None else "null"] += 1
            if (r.get("correction") or "").strip():
                with_correction += 1
            if _is_exportable_pair(r):
                exportable += 1
            ask = is_ask_local_record(r)
            outcome = is_outcome_record(r)
            if ask:
                ask_local.append(r)
            if outcome and not ask:
                agent_outcome.append(r)
            if src == "mcp":
                mcp_rows.append(r)
            if src == "ui":
                ui_rows.append(r)
            dom = record_domain(r)
            if dom:
                domain_rows[dom].append(r)
            if rating in (1, -1):
                try:
                    day = datetime.fromtimestamp(
                        float(r.get("created_at") or 0), tz=timezone.utc
                    ).strftime("%Y-%m-%d")
                except (TypeError, ValueError, OSError):
                    day = "unknown"
                by_day[day][rating] += 1

        days_out = []
        for day in sorted(by_day.keys())[-max(1, int(day_limit)) :]:
            c = by_day[day]
            pos = int(c.get(1, 0))
            neg = int(c.get(-1, 0))
            judged = pos + neg
            days_out.append(
                {
                    "day": day,
                    "pos": pos,
                    "neg": neg,
                    "judged": judged,
                    "accuracy": (pos / judged) if judged else None,
                }
            )

        slices: Dict[str, Any] = {
            "all": _rating_bucket(rows),
            "ask_local": _rating_bucket(ask_local),
            "agent_outcome": _rating_bucket(agent_outcome),
            "mcp": _rating_bucket(mcp_rows),
            "ui": _rating_bucket(ui_rows),
        }
        for dom in sorted(domain_rows):
            slices[dom] = _rating_bucket(domain_rows[dom])

        return {
            "count": len(rows),
            "path": str(self.path),
            "by_rating": {
                "-1": int(by_rating.get(-1, 0)),
                "0": int(by_rating.get(0, 0)),
                "1": int(by_rating.get(1, 0)),
            },
            "by_source": dict(by_source),
            "by_kind": dict(by_kind),
            "slices": slices,
            "by_day": days_out,
            "exportable_pairs": exportable,
            "with_correction": with_correction,
        }

    def list_records(
        self,
        *,
        rating: Optional[int] = None,
        source: Optional[str] = None,
        kind: Optional[str] = None,
        slice_name: Optional[str] = None,
        domain: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "desc",
        preview_chars: int = 240,
    ) -> Dict[str, Any]:
        self.reload_if_stale()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        with self._lock:
            rows = list(self._records)

        filtered: List[Dict[str, Any]] = []
        ql = (q or "").strip().lower()
        for r in rows:
            if rating is not None:
                try:
                    if int(r.get("rating", 0)) != int(rating):
                        continue
                except (TypeError, ValueError):
                    continue
            if source and str(r.get("source") or "") != source:
                continue
            meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            rec_kind = meta.get("kind")
            if kind == "outcome" and rec_kind != "outcome":
                continue
            if kind == "other" and rec_kind == "outcome":
                continue
            ask = is_ask_local_record(r)
            outcome = is_outcome_record(r)
            if slice_name == "ask_local" and not ask:
                continue
            if slice_name == "agent_outcome" and not (outcome and not ask):
                continue
            if slice_name == "mcp" and str(r.get("source") or "") != "mcp":
                continue
            if slice_name == "ui" and str(r.get("source") or "") != "ui":
                continue
            if slice_name in VALID_DOMAINS and record_domain(r) != slice_name:
                continue
            if domain and record_domain(r) != domain:
                continue
            if ql:
                blob = " ".join(
                    [
                        str(r.get("prompt") or ""),
                        str(r.get("response") or ""),
                        str(r.get("correction") or ""),
                    ]
                ).lower()
                if ql not in blob:
                    continue
            filtered.append(r)

        reverse = order != "asc"
        filtered.sort(key=lambda x: float(x.get("created_at") or 0), reverse=reverse)
        total = len(filtered)
        page = filtered[offset : offset + limit]
        items = []
        for r in page:
            items.append(
                {
                    "feedback_id": r.get("feedback_id"),
                    "created_at": r.get("created_at"),
                    "rating": r.get("rating"),
                    "source": r.get("source"),
                    "model": r.get("model"),
                    "tier": r.get("tier"),
                    "event_id": r.get("event_id"),
                    "completion_id": r.get("completion_id"),
                    "metadata": r.get("metadata") or {},
                    "slice_tags": slice_tags_for(r),
                    "has_correction": bool((r.get("correction") or "").strip()),
                    "prompt_preview": _preview(r.get("prompt"), preview_chars),
                    "response_preview": _preview(r.get("response"), preview_chars),
                    "correction_preview": (
                        _preview(r.get("correction"), preview_chars)
                        if (r.get("correction") or "").strip()
                        else None
                    ),
                }
            )
        return {
            "n_total": total,
            "n_returned": len(items),
            "offset": offset,
            "limit": limit,
            "items": items,
        }

    def similar_corrections(
        self, prompt: str, *, limit: Optional[int] = None, domain: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return past corrections, ranked by token overlap with the prompt."""
        self.reload_if_stale()
        lim = self.max_inject if limit is None else max(0, limit)
        if lim <= 0 or not prompt.strip():
            return []
        q = _tokenize_text(prompt)
        if not q:
            return []
        hint_domain = domain or infer_domain_from_text(prompt)
        scored: List[Tuple[float, Dict[str, Any]]] = []
        with self._lock:
            for rec in self._records:
                correction = (rec.get("correction") or "").strip()
                if not correction:
                    continue
                rating = rec.get("rating")
                if rating is not None and int(rating) >= 0:
                    if not rec.get("preferred") and not rec.get("chosen"):
                        continue
                hay = " ".join(
                    [
                        str(rec.get("prompt") or ""),
                        str(rec.get("response") or ""),
                        correction,
                    ]
                )
                toks = _tokenize_text(hay)
                if not toks:
                    continue
                overlap = len(q & toks) / float(len(q))
                if overlap <= 0:
                    continue
                if hint_domain:
                    rec_dom = record_domain(rec) or infer_domain_from_text(hay)
                    if rec_dom == hint_domain:
                        overlap += 0.15
                scored.append((overlap, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:lim]]

    def few_shot_system_note(self, prompt: str) -> Optional[str]:
        hint = infer_domain_from_text(prompt)
        matches = self.similar_corrections(prompt, domain=hint)
        if not matches:
            return None
        parts = [
            "Apply lessons from prior corrected answers when relevant "
            "(operator / MCP reinforcement feedback on the local Jarvis model):",
        ]
        for i, rec in enumerate(matches, 1):
            bad = (rec.get("response") or "").strip()
            good = (
                rec.get("correction") or rec.get("preferred") or rec.get("chosen") or ""
            ).strip()
            parts.append(f"{i}. Incorrect: {bad[:400]}\n   Correct: {good[:400]}")
        return "\n".join(parts)
