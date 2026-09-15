"""Fusion 360 tool library import and NFS-backed catalog.

Accepts Fusion ``.tools`` (ZIP containing ``tools.json``) or raw JSON exports.
Tools are upserted by Fusion ``guid`` into a catalog under the session PVC so
the library is node-agnostic and survives pod restarts.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

from loguru import logger


def _tool_library_dir(session_store_dir: Path) -> Path:
    """Keep library on the same PVC as sessions (mounted at session_store_dir)."""
    return Path(session_store_dir) / "tool_library"


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    if isinstance(data, dict):
        for key in ("data", "tools", "library"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [t for t in inner if isinstance(t, dict)]
            if isinstance(inner, dict) and isinstance(inner.get("data"), list):
                return [t for t in inner["data"] if isinstance(t, dict)]
    return []


def extract_fusion_tools_bytes(raw: bytes, filename: str = "") -> tuple[list[dict[str, Any]], str]:
    """
    Parse upload bytes into Fusion tool objects.

    Returns (tools, source_label).
    """
    name = (filename or "").lower()
    # ZIP / .tools
    if name.endswith(".tools") or name.endswith(".zip") or (len(raw) >= 2 and raw[:2] == b"PK"):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            preferred = [
                n
                for n in names
                if Path(n).name.lower() in ("tools.json", "tool.json", "library.json")
            ]
            json_names = preferred or [n for n in names if n.lower().endswith(".json")]
            if not json_names:
                raise ValueError("Fusion .tools archive contains no JSON")
            member = json_names[0]
            text = zf.read(member).decode("utf-8", errors="replace")
            tools = _as_list(json.loads(text))
            if not tools:
                raise ValueError(f"No tools found in {member}")
            return tools, member
    # Raw JSON
    text = raw.decode("utf-8", errors="replace")
    tools = _as_list(json.loads(text))
    if not tools:
        raise ValueError("JSON contains no tools")
    return tools, filename or "tools.json"


def _post_process_number(tool: dict[str, Any]) -> int | None:
    for key in ("post-process", "post_process", "postProcess", "postprocess"):
        pp = tool.get(key)
        if isinstance(pp, dict):
            for nk in ("number", "tool-number", "toolNumber", "tool_number"):
                if nk in pp and pp[nk] is not None:
                    try:
                        return int(pp[nk])
                    except (TypeError, ValueError):
                        pass
        elif isinstance(pp, (int, float)):
            return int(pp)
    for nk in ("number", "tool-number", "toolNumber"):
        if nk in tool and tool[nk] is not None:
            try:
                return int(tool[nk])
            except (TypeError, ValueError):
                pass
    return None


def _first_float(*candidates: Any) -> float | None:
    for c in candidates:
        if c is None:
            continue
        try:
            return float(c)
        except (TypeError, ValueError):
            continue
    return None


def _unit_is_inch(unit: str | None) -> bool:
    if not unit:
        return False
    return unit.lower() in ("inches", "inch", "in")


def _unit_is_mm(unit: str | None) -> bool:
    if not unit:
        return False
    return unit.lower() in ("millimeters", "millimeter", "mm")


def _length_to_mm(value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    if _unit_is_inch(unit):
        return float(value) * 25.4
    # mm unit or unknown: leave as-is (do not invent inch conversion for chipload)
    return float(value)


def _feed_to_mm_min(value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    if _unit_is_inch(unit):
        return float(value) * 25.4
    return float(value)


def _iter_cutting_presets(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Yield Fusion cutting preset dicts (nested start-values.presets preferred)."""
    out: list[dict[str, Any]] = []
    for key in ("start-values", "start_values", "startValues", "startvalues"):
        sv = raw.get(key)
        if not isinstance(sv, dict):
            continue
        presets = sv.get("presets")
        if isinstance(presets, list):
            out.extend(p for p in presets if isinstance(p, dict))
        else:
            out.append(sv)
    # fallbacks: top-level aliases
    if not out:
        out.append(raw)
    return out


def _extract_cutting_preset(raw: dict[str, Any], unit: str | None) -> dict[str, Any]:
    """
    Normalize Fusion feed / chipload / spindle RPM from cutting presets.

    Prefers nested ``start-values.presets[]`` fields ``v_f``, ``f_z``, ``n`` and
    common English aliases. Lengths converted to mm / mm/min using tool unit.
    """
    feed_mm_min: float | None = None
    chipload_mm: float | None = None
    ref_rpm: float | None = None
    preset_name: str | None = None
    for preset in _iter_cutting_presets(raw):
        if feed_mm_min is None:
            feed_raw = _first_float(
                preset.get("v_f"),
                preset.get("feedRate"),
                preset.get("feed-rate"),
                preset.get("feed_rate"),
                preset.get("cutting-feedrate"),
                preset.get("cuttingFeedrate"),
                preset.get("cutting_feedrate"),
            )
            feed_mm_min = _feed_to_mm_min(feed_raw, unit)
        if chipload_mm is None:
            fz_raw = _first_float(
                preset.get("f_z"),
                preset.get("feedPerTooth"),
                preset.get("feed-per-tooth"),
                preset.get("feed_per_tooth"),
                preset.get("chipload"),
                preset.get("chipLoad"),
            )
            chipload_mm = _length_to_mm(fz_raw, unit)
        if ref_rpm is None:
            rpm = _first_float(
                preset.get("n"),
                preset.get("spindleSpeed"),
                preset.get("spindle-speed"),
                preset.get("spindle_speed"),
                preset.get("rpm"),
            )
            if rpm is not None and rpm > 0:
                ref_rpm = rpm
        if preset_name is None:
            name = preset.get("name") or preset.get("description")
            if name:
                preset_name = str(name)
        if feed_mm_min is not None and chipload_mm is not None and ref_rpm is not None:
            break

    # post-process / top-level RPM fallbacks
    if ref_rpm is None:
        for key in ("post-process", "post_process", "postProcess"):
            pp = raw.get(key)
            if isinstance(pp, dict):
                rpm = _first_float(
                    pp.get("spindleSpeed"),
                    pp.get("spindle-speed"),
                    pp.get("rpm"),
                )
                if rpm is not None and rpm > 0:
                    ref_rpm = rpm
                    break
    if ref_rpm is None:
        rpm = _first_float(
            raw.get("spindleRpm"),
            raw.get("spindle-speed"),
            raw.get("spindleSpeed"),
            raw.get("rpm"),
        )
        if rpm is not None and rpm > 0:
            ref_rpm = rpm

    return {
        "feed_mm_min": feed_mm_min,
        "chipload_mm": chipload_mm,
        "reference_rpm": ref_rpm,
        "preset_name": preset_name,
    }


def normalize_fusion_tool(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten Fusion tool into a searchable catalog record (keep raw under ``fusion``)."""
    guid = str(raw.get("guid") or raw.get("GUID") or "").strip()
    if not guid:
        # Fallback identity so imports without guid still work (rare)
        desc = str(raw.get("description") or raw.get("vendor") or "unknown")
        pid = str(raw.get("product-id") or raw.get("productId") or "")
        guid = f"local:{desc}:{pid}"[:120]

    geom = raw.get("geometry") if isinstance(raw.get("geometry"), dict) else {}
    holder = raw.get("holder") if isinstance(raw.get("holder"), dict) else {}
    diameter = geom.get("DC")
    flutes = geom.get("NOF")
    try:
        diameter_f = float(diameter) if diameter is not None else None
    except (TypeError, ValueError):
        diameter_f = None
    try:
        flutes_i = int(flutes) if flutes is not None else None
    except (TypeError, ValueError):
        flutes_i = None

    unit = str(raw.get("unit") or geom.get("unit") or "").strip() or None
    # Diameter from Fusion geometry.DC is typically in the tool's unit (inch or mm).
    diameter_mm = None
    if diameter_f is not None:
        if _unit_is_inch(unit):
            diameter_mm = diameter_f * 25.4
        elif _unit_is_mm(unit):
            diameter_mm = diameter_f
        else:
            # Heuristic: values < 5 without unit are often inches for EM tools.
            diameter_mm = diameter_f * 25.4 if diameter_f < 5.0 else diameter_f

    cutting = _extract_cutting_preset(raw, unit)
    ref_rpm = cutting["reference_rpm"]

    return {
        "guid": guid,
        "description": str(raw.get("description") or "").strip() or None,
        "type": str(raw.get("type") or "").strip() or None,
        "vendor": str(raw.get("vendor") or "").strip() or None,
        "product_id": str(raw.get("product-id") or raw.get("productId") or "").strip() or None,
        "bmc": str(raw.get("BMC") or raw.get("bmc") or "").strip() or None,
        "diameter": diameter_f,
        "diameter_mm": diameter_mm,
        "flutes": flutes_i,
        "tool_number": _post_process_number(raw),
        "holder": str(holder.get("description") or "").strip() or None,
        "unit": unit,
        "feed_mm_min": cutting["feed_mm_min"],
        "chipload_mm": cutting["chipload_mm"],
        "cutting_preset_name": cutting["preset_name"],
        "reference_rpm": ref_rpm,
        "reference_rpm_source": "fusion_reference" if ref_rpm is not None else None,
        "fusion": raw,
    }


class ToolLibraryStore:
    """Persistent Fusion tool catalog on the sessions PVC."""

    def __init__(self, session_store_dir: Path) -> None:
        self.root = _tool_library_dir(session_store_dir)
        self.catalog_path = self.root / "catalog.json"
        self.originals_dir = self.root / "originals"
        self.root.mkdir(parents=True, exist_ok=True)
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        self._catalog = self._load()

    def _empty(self) -> dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "n_tools": 0,
            "tools": {},
            "imports": [],
        }

    def _load(self) -> dict[str, Any]:
        if not self.catalog_path.is_file():
            return self._empty()
        try:
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return self._empty()
            tools = data.get("tools")
            if not isinstance(tools, dict):
                data["tools"] = {}
            if not isinstance(data.get("imports"), list):
                data["imports"] = []
            return data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load tool catalog: {}", e)
            return self._empty()

    def _save(self) -> None:
        self._catalog["n_tools"] = len(self._catalog.get("tools") or {})
        self._catalog["updated_at"] = time.time()
        tmp = self.catalog_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._catalog, indent=2), encoding="utf-8")
        tmp.replace(self.catalog_path)

    def import_bytes(
        self,
        raw: bytes,
        *,
        filename: str = "",
        replace: bool = False,
    ) -> dict[str, Any]:
        tools_raw, source_label = extract_fusion_tools_bytes(raw, filename=filename)
        normalized = [normalize_fusion_tool(t) for t in tools_raw]
        prev_map: dict[str, Any] = dict(self._catalog.get("tools") or {})
        created = 0
        updated = 0
        removed = 0
        replaced_duplicates = 0

        if replace:
            tools_map: dict[str, Any] = {}
            incoming_guids = {rec["guid"] for rec in normalized}
            removed = sum(1 for g in prev_map if g not in incoming_guids)
            for rec in normalized:
                guid = rec["guid"]
                if guid in prev_map:
                    updated += 1
                else:
                    created += 1
                rec["imported_at"] = time.time()
                tools_map[guid] = rec
            # Within a replace upload, still enforce unique tool_number (last wins)
            by_number: dict[int, str] = {}
            for guid, rec in list(tools_map.items()):
                tn = rec.get("tool_number")
                if tn is None:
                    continue
                try:
                    tn_i = int(tn)
                except (TypeError, ValueError):
                    continue
                if tn_i in by_number and by_number[tn_i] != guid:
                    old = by_number[tn_i]
                    tools_map.pop(old, None)
                    replaced_duplicates += 1
                    removed += 1
                by_number[tn_i] = guid
            mode = "replace"
        else:
            tools_map = dict(prev_map)
            for rec in normalized:
                guid = rec["guid"]
                tn = rec.get("tool_number")
                if tn is not None:
                    try:
                        tn_i = int(tn)
                    except (TypeError, ValueError):
                        tn_i = None
                    if tn_i is not None:
                        for other_guid, other in list(tools_map.items()):
                            if other_guid == guid:
                                continue
                            if other.get("tool_number") == tn_i:
                                tools_map.pop(other_guid, None)
                                replaced_duplicates += 1
                                removed += 1
                if guid in tools_map:
                    updated += 1
                else:
                    created += 1
                rec["imported_at"] = time.time()
                tools_map[guid] = rec
            mode = "upsert"

        # Preserve original upload
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe_name = Path(filename or "library.tools").name.replace("/", "_")
        orig_path = self.originals_dir / f"{stamp}_{safe_name}"
        try:
            orig_path.write_bytes(raw)
        except OSError as e:
            logger.warning("Failed to save original library upload: {}", e)
            orig_path = None

        import_rec = {
            "ts": time.time(),
            "filename": filename or None,
            "source_label": source_label,
            "mode": mode,
            "n_parsed": len(normalized),
            "created": created,
            "updated": updated,
            "removed": removed,
            "replaced_duplicates": replaced_duplicates,
            "original": str(orig_path.name) if orig_path else None,
        }
        imports = list(self._catalog.get("imports") or [])
        imports.append(import_rec)
        # Keep last 50 import records
        self._catalog["imports"] = imports[-50:]
        self._catalog["tools"] = tools_map
        self._save()
        return {
            "n_parsed": len(normalized),
            "created": created,
            "updated": updated,
            "removed": removed,
            "replaced_duplicates": replaced_duplicates,
            "mode": mode,
            "n_tools": len(tools_map),
            "source_label": source_label,
            "filename": filename or None,
        }

    def delete_tool(self, guid: str) -> dict[str, Any]:
        tools_map: dict[str, Any] = dict(self._catalog.get("tools") or {})
        if guid not in tools_map:
            raise KeyError(guid)
        tools_map.pop(guid)
        self._catalog["tools"] = tools_map
        self._save()
        return {"guid": guid, "deleted": True, "n_tools": len(tools_map)}

    def patch_tool(self, guid: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Patch allowlisted summary fields; enforce unique tool_number."""
        allowed = {
            "description",
            "tool_number",
            "flutes",
            "diameter",
            "diameter_mm",
            "feed_per_tooth",
            "feed_per_tooth_mm",
            "chipload",
            "chipload_mm",
            "reference_rpm",
            "reference_rpm_source",
            "vendor",
            "product_id",
            "type",
            "holder",
        }
        tools_map: dict[str, Any] = dict(self._catalog.get("tools") or {})
        tool = tools_map.get(guid)
        if tool is None:
            raise KeyError(guid)
        patched = dict(tool)
        applied: dict[str, Any] = {}
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "tool_number" and value is not None:
                try:
                    value = int(value)
                except (TypeError, ValueError) as e:
                    raise ValueError(f"invalid tool_number: {value}") from e
            patched[key] = value
            applied[key] = value

        tn = patched.get("tool_number")
        removed_dupes = 0
        if tn is not None:
            try:
                tn_i = int(tn)
            except (TypeError, ValueError):
                tn_i = None
            if tn_i is not None:
                for other_guid, other in list(tools_map.items()):
                    if other_guid == guid:
                        continue
                    if other.get("tool_number") == tn_i:
                        tools_map.pop(other_guid, None)
                        removed_dupes += 1

        tools_map[guid] = patched
        self._catalog["tools"] = tools_map
        self._save()
        out = {k: v for k, v in patched.items() if k != "fusion"}
        out["patched"] = applied
        out["replaced_duplicates"] = removed_dupes
        return out

    def list_tools(self, *, q: str | None = None) -> list[dict[str, Any]]:
        tools = list((self._catalog.get("tools") or {}).values())
        if q:
            ql = q.lower()
            tools = [
                t
                for t in tools
                if ql in (t.get("description") or "").lower()
                or ql in (t.get("vendor") or "").lower()
                or ql in (t.get("product_id") or "").lower()
                or ql in (t.get("type") or "").lower()
                or ql in (t.get("guid") or "").lower()
                or (t.get("tool_number") is not None and ql == str(t.get("tool_number")))
            ]
        # Summary without full fusion blob for list
        out = []
        for t in tools:
            out.append({k: v for k, v in t.items() if k != "fusion"})
        return sorted(
            out,
            key=lambda x: (
                x.get("tool_number") is None,
                x.get("tool_number") or 0,
                (x.get("description") or "").lower(),
            ),
        )

    def get_tool(self, guid: str) -> dict[str, Any] | None:
        return (self._catalog.get("tools") or {}).get(guid)

    def lookup_by_number(self, number: int) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in t.items() if k != "fusion"}
            for t in (self._catalog.get("tools") or {}).values()
            if t.get("tool_number") == number
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "n_tools": len(self._catalog.get("tools") or {}),
            "updated_at": self._catalog.get("updated_at"),
            "n_imports": len(self._catalog.get("imports") or []),
            "last_import": (self._catalog.get("imports") or [None])[-1],
        }
