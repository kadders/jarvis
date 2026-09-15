"""Stream-parse tap / live-spindle CSV (and zip run bundles) into numpy arrays."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from models.rpm_provenance import make_rpm_sample


def infer_sample_rate_hz(t: np.ndarray, hint: float = 0.0) -> float:
    if hint and hint > 0:
        return float(hint)
    if t.size < 2:
        return 0.0
    dt = np.diff(t)
    dt = dt[dt > 0]
    if dt.size == 0:
        return 0.0
    return float(1.0 / np.median(dt))


def parse_tap_csv_text(text: str | bytes) -> tuple[np.ndarray, np.ndarray, float]:
    """Parse CSV content with columns t_s, ax_g, ay_g, az_g and optional # sample_rate_hz."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return parse_tap_csv_file(io.StringIO(text))


def parse_tap_csv_file(f) -> tuple[np.ndarray, np.ndarray, float]:
    """
    Stream-parse an open text file / StringIO.

    Returns:
        t: (N,) time in seconds
        data: (3, N) ax, ay, az in g
        sample_rate_hz: from comment row or inferred from t
    """
    reader = csv.reader(f)
    try:
        next(reader)  # header
    except StopIteration as e:
        raise ValueError("CSV is empty") from e

    sample_rate_hz = 0.0
    t_list: list[float] = []
    ax_list: list[float] = []
    ay_list: list[float] = []
    az_list: list[float] = []

    first = True
    for row in reader:
        if not row:
            continue
        cell0 = str(row[0]).strip()
        if first and cell0.startswith("#"):
            first = False
            try:
                sample_rate_hz = float(row[1])
            except (IndexError, ValueError):
                pass
            continue
        first = False
        if cell0.startswith("#"):
            continue
        if len(row) < 4:
            continue
        try:
            t_list.append(float(row[0]))
            ax_list.append(float(row[1]))
            ay_list.append(float(row[2]))
            az_list.append(float(row[3]))
        except ValueError:
            continue

    if not t_list:
        raise ValueError("No data rows in CSV")

    t = np.asarray(t_list, dtype=np.float64)
    data = np.vstack(
        [
            np.asarray(ax_list, dtype=np.float64),
            np.asarray(ay_list, dtype=np.float64),
            np.asarray(az_list, dtype=np.float64),
        ]
    )
    sr = infer_sample_rate_hz(t, sample_rate_hz)
    return t, data, sr


def parse_tap_csv_path(path: Path) -> tuple[np.ndarray, np.ndarray, float]:
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return parse_tap_csv_file(f)


def extract_csv_from_zip(data: bytes) -> tuple[bytes, str]:
    """
    Prefer homing.csv, else first *.csv in the archive.
    Returns (csv_bytes, member_name).
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/") and n.lower().endswith(".csv")]
        if not names:
            raise ValueError("ZIP contains no CSV files")
        preferred = [n for n in names if Path(n).name.lower() == "homing.csv"]
        pick = preferred[0] if preferred else sorted(names)[0]
        return zf.read(pick), pick


def _zip_member_by_basename(zf: zipfile.ZipFile, basename: str) -> tuple[str, bytes] | None:
    want = basename.lower()
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        if Path(name).name.lower() == want:
            return name, zf.read(name)
    return None


def parse_tool_events_jsonl(text: str | bytes) -> list[dict[str, Any]]:
    """Parse tool-events.jsonl into a list of event dicts."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def parse_tools_snapshot_json(text: str | bytes) -> list[dict[str, Any]]:
    """Parse tools-snapshot.json; returns the tools list."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    if isinstance(data, dict):
        tools = data.get("tools")
        if isinstance(tools, list):
            return [t for t in tools if isinstance(t, dict)]
    return []


def parse_run_meta_json(text: str | bytes) -> dict[str, Any]:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _hz_to_rpm(freq_hz: float, poles: float = 2.0) -> float:
    """
    Convert VFD electrical frequency to RPM.

    Identity: RPM = 120 × Hz / poles (2-pole 400 Hz → 24000 RPM).
    """
    p = float(poles) if poles and float(poles) > 0 else 2.0
    return float(freq_hz) * 120.0 / p


def _row_t_s(row: dict[str, Any]) -> float | None:
    try:
        if row.get("t_s") is None or row.get("t_s") == "":
            return None
        return float(row["t_s"])
    except (TypeError, ValueError):
        return None


def _row_registers(row: dict[str, Any]) -> dict[str, Any]:
    """Accept flat CSV-style rows or MQTT ``{t_s, registers: {...}}`` payloads."""
    regs = row.get("registers")
    if isinstance(regs, dict):
        return regs
    return row


def modbus_row_to_rpm_samples(
    row: dict[str, Any],
    *,
    poles: float = 2.0,
) -> list[dict[str, Any]]:
    """
    Normalize one Modbus poll into typed RPM samples (measured / commanded).

    H100 conventions (see tap-testing docs/MODBUS_H100_VFD.md):
    - hr_513 / ir_0 / ir_1 are **always deci-Hz** (value × 10)
    - RPM = 120 × (raw/10) / poles
    """
    t_s = _row_t_s(row)
    if t_s is None:
        return []
    cols = _row_registers(row)
    samples: list[dict[str, Any]] = []

    # Commanded: hr_513 = frequency × 10
    if "hr_513" in cols and cols["hr_513"] not in ("", None):
        try:
            raw = float(cols["hr_513"])
            freq_hz = raw / 10.0
            rpm = _hz_to_rpm(freq_hz, poles=poles)
            if rpm > 0:
                samples.append(
                    make_rpm_sample(
                        t_s,
                        rpm,
                        source="commanded_modbus",
                        raw_value=raw,
                        scaling="hr_513_deci_hz",
                        note="H100 holding 513 command frequency (deci-Hz)",
                    )
                )
        except (TypeError, ValueError):
            pass

    # Measured: ir_0 output frequency, always deci-Hz
    if "ir_0" in cols and cols["ir_0"] not in ("", None):
        try:
            raw = float(cols["ir_0"])
            freq_hz = raw / 10.0
            rpm = _hz_to_rpm(freq_hz, poles=poles)
            if rpm > 0:
                samples.append(
                    make_rpm_sample(
                        t_s,
                        rpm,
                        source="measured_modbus",
                        raw_value=raw,
                        scaling="ir_0_deci_hz",
                        note="H100 input register 0 (deci-Hz)",
                    )
                )
        except (TypeError, ValueError):
            pass

    return samples


def spindle_payload_to_samples(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Split one ArborCTL spindle MQTT/JSONL object into rpm_samples + load_samples.
    """
    rpm_out: list[dict[str, Any]] = []
    load_out: list[dict[str, Any]] = []
    try:
        t_s = float(data.get("t_s"))
    except (TypeError, ValueError):
        return rpm_out, load_out
    if t_s < 0 or t_s >= 1e9:
        return rpm_out, load_out

    src = str(data.get("source") or "arborctl")
    poles = data.get("poles")
    rpm = data.get("rpm")
    if rpm is not None:
        try:
            rpm_f = float(rpm)
        except (TypeError, ValueError):
            rpm_f = None
        else:
            rpm_src = "arborctl" if src == "arborctl" else "rrf_spindle"
            hz = data.get("hz")
            try:
                hz_f = float(hz) if hz is not None else None
            except (TypeError, ValueError):
                hz_f = None
            rpm_out.append(
                make_rpm_sample(
                    t_s,
                    rpm_f,
                    source=rpm_src,
                    raw_value=hz_f,
                    scaling="arborctl_status" if rpm_src == "arborctl" else "rrf_spindles_current",
                    note=f"poles={poles}" if poles is not None else None,
                )
            )
    cmd = data.get("commanded_rpm")
    if cmd is not None:
        try:
            cmd_f = float(cmd)
        except (TypeError, ValueError):
            cmd_f = None
        else:
            chz = data.get("commanded_hz")
            try:
                chz_f = float(chz) if chz is not None else None
            except (TypeError, ValueError):
                chz_f = None
            rpm_out.append(
                make_rpm_sample(
                    t_s,
                    cmd_f,
                    source="commanded_rrf",
                    raw_value=chz_f,
                    scaling="rrf_spindles_active",
                    note="RRF spindles.active",
                )
            )
    if data.get("power_available"):
        load_out.append(
            {
                "t_s": t_s,
                "load_percent": data.get("load_percent"),
                "watts": data.get("watts"),
            }
        )
    return rpm_out, load_out


def parse_spindle_telemetry_jsonl(
    text: str | bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse spindle-telemetry.jsonl into rpm_samples and load_samples."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    rpm_samples: list[dict[str, Any]] = []
    load_samples: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        r, l = spindle_payload_to_samples(obj)
        rpm_samples.extend(r)
        load_samples.extend(l)
    return rpm_samples, load_samples


def parse_modbus_csv_text(
    text: str | bytes,
    *,
    poles: float = 2.0,
) -> list[dict[str, Any]]:
    """
    Parse modbus.csv into typed RPM samples (measured / commanded).

    Uses :func:`modbus_row_to_rpm_samples` so CSV import and live MQTT cannot drift.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header: list[str] | None = None
    samples: list[dict[str, Any]] = []
    for row in reader:
        if not row:
            continue
        if str(row[0]).strip().startswith("#"):
            continue
        if header is None:
            header = [c.strip() for c in row]
            continue
        if len(row) < 2:
            continue
        try:
            t_s = float(row[0])
        except ValueError:
            continue
        cols = {header[i]: row[i] for i in range(min(len(header), len(row)))}
        flat = {"t_s": t_s, **cols}
        samples.extend(modbus_row_to_rpm_samples(flat, poles=poles))

    return samples


def load_run_bundle(
    raw: bytes,
    *,
    filename: str = "",
) -> dict[str, Any]:
    """
    Load a CSV or ZIP run bundle.

    Returns dict with:
      t, data, sample_rate_hz, source_label,
      tool_events, tools_snapshot, run_meta, rpm_samples, sidecars
    """
    name = (filename or "").lower()
    empty_sidecars: dict[str, Any] = {
        "tool_events": [],
        "tools_snapshot": [],
        "run_meta": {},
        "rpm_samples": [],
        "load_samples": [],
        "members": [],
    }

    if name.endswith(".zip") or (len(raw) >= 4 and raw[:2] == b"PK"):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            members = [n for n in zf.namelist() if not n.endswith("/")]
            csv_names = [n for n in members if n.lower().endswith(".csv")]
            if not csv_names:
                raise ValueError("ZIP contains no CSV files")
            preferred = [n for n in csv_names if Path(n).name.lower() == "homing.csv"]
            # Prefer accel CSV over modbus.csv
            accel_candidates = [
                n
                for n in (preferred or csv_names)
                if Path(n).name.lower() != "modbus.csv"
            ]
            pick = accel_candidates[0] if accel_candidates else sorted(csv_names)[0]
            csv_bytes = zf.read(pick)
            t, data, sr = parse_tap_csv_text(csv_bytes)

            tool_events: list[dict[str, Any]] = []
            tools_snapshot: list[dict[str, Any]] = []
            run_meta: dict[str, Any] = {}
            rpm_samples: list[dict[str, Any]] = []
            load_samples: list[dict[str, Any]] = []
            found: list[str] = [pick]

            te = _zip_member_by_basename(zf, "tool-events.jsonl")
            if te:
                found.append(te[0])
                tool_events = parse_tool_events_jsonl(te[1])

            ts = _zip_member_by_basename(zf, "tools-snapshot.json")
            if ts:
                found.append(ts[0])
                tools_snapshot = parse_tools_snapshot_json(ts[1])

            rm = _zip_member_by_basename(zf, "run-meta.json")
            if rm:
                found.append(rm[0])
                run_meta = parse_run_meta_json(rm[1])
                if run_meta.get("sample_rate_hz") and (not sr or sr <= 0):
                    try:
                        sr = float(run_meta["sample_rate_hz"])
                    except (TypeError, ValueError):
                        pass

            mb = _zip_member_by_basename(zf, "modbus.csv")
            if mb:
                found.append(mb[0])
                rpm_samples = parse_modbus_csv_text(mb[1])

            stl = _zip_member_by_basename(zf, "spindle-telemetry.jsonl")
            if stl:
                found.append(stl[0])
                sp_rpm, sp_load = parse_spindle_telemetry_jsonl(stl[1])
                rpm_samples.extend(sp_rpm)
                load_samples.extend(sp_load)

            # RRF tool snapshot spindleRpm / spindle_rpm as reference samples at t=0
            for tool in tools_snapshot:
                num = tool.get("number")
                rpm = tool.get("spindleRpm")
                if rpm is None:
                    rpm = tool.get("spindle_rpm")
                if rpm is None:
                    continue
                try:
                    rpm_f = float(rpm)
                except (TypeError, ValueError):
                    continue
                if rpm_f <= 0:
                    continue
                rpm_samples.append(
                    make_rpm_sample(
                        0.0,
                        rpm_f,
                        source="rrf_spindle",
                        raw_value=rpm_f,
                        scaling=None,
                        note=f"RRF tools-snapshot T{num}",
                    )
                )

            return {
                "t": t,
                "data": data,
                "sample_rate_hz": sr,
                "source_label": pick,
                "tool_events": tool_events,
                "tools_snapshot": tools_snapshot,
                "run_meta": run_meta,
                "rpm_samples": rpm_samples,
                "load_samples": load_samples,
                "sidecars": {"members": found},
            }

    t, data, sr = parse_tap_csv_text(raw)
    return {
        "t": t,
        "data": data,
        "sample_rate_hz": sr,
        "source_label": filename or "upload.csv",
        "tool_events": [],
        "tools_snapshot": [],
        "run_meta": {},
        "rpm_samples": [],
        "load_samples": [],
        "sidecars": empty_sidecars,
    }


def load_upload_bytes(
    raw: bytes,
    *,
    filename: str = "",
) -> tuple[np.ndarray, np.ndarray, float, str]:
    """
    Load CSV or ZIP upload (accel only; backward-compatible).

    Returns t, data (3,N), sample_rate_hz, source_label (member or filename).
    """
    bundle = load_run_bundle(raw, filename=filename)
    return (
        bundle["t"],
        bundle["data"],
        bundle["sample_rate_hz"],
        bundle["source_label"],
    )


def downsample_series(
    t: np.ndarray,
    data: np.ndarray,
    max_points: int,
) -> dict[str, list[float]]:
    """Uniform stride downsample for chart payloads."""
    n = int(t.size)
    if n == 0:
        return {"t_s": [], "ax_g": [], "ay_g": [], "az_g": [], "n_total": 0, "n_returned": 0}
    max_points = max(8, int(max_points))
    step = max(1, (n + max_points - 1) // max_points)
    idx = np.arange(0, n, step)
    return {
        "t_s": t[idx].astype(float).tolist(),
        "ax_g": data[0, idx].astype(float).tolist(),
        "ay_g": data[1, idx].astype(float).tolist(),
        "az_g": data[2, idx].astype(float).tolist(),
        "n_total": n,
        "n_returned": int(idx.size),
        "stride": int(step),
    }


def save_timeseries_npz(
    path: Path,
    t: np.ndarray,
    data: np.ndarray,
    sample_rate_hz: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        t=t.astype(np.float64),
        ax=data[0].astype(np.float64),
        ay=data[1].astype(np.float64),
        az=data[2].astype(np.float64),
        sample_rate_hz=np.asarray(sample_rate_hz, dtype=np.float64),
    )


def load_timeseries_npz(path: Path) -> tuple[np.ndarray, np.ndarray, float]:
    with np.load(path) as z:
        t = np.asarray(z["t"], dtype=np.float64)
        data = np.vstack(
            [
                np.asarray(z["ax"], dtype=np.float64),
                np.asarray(z["ay"], dtype=np.float64),
                np.asarray(z["az"], dtype=np.float64),
            ]
        )
        sr = float(z["sample_rate_hz"]) if "sample_rate_hz" in z.files else 0.0
    if sr <= 0:
        sr = infer_sample_rate_hz(t)
    return t, data, sr
