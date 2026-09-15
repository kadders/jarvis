"""CAM G-code path normalization, index, and resolve policy."""

from __future__ import annotations

from pathlib import Path

from models.gcode_source import normalize_job_file, resolve_cam_gcode
from models.gcode_summary import sha256_bytes, summarize_gcode_text


def test_normalize_job_file_strips_gcodes_prefix():
    base, rel = normalize_job_file("0:/gcodes/part.gcode")
    assert base == "part.gcode"
    assert rel == "part.gcode"
    base2, rel2 = normalize_job_file("gcodes/nested/part.gcode")
    assert base2 == "part.gcode"
    assert rel2 == "nested/part.gcode"
    assert normalize_job_file("0:/gcodes/../etc/passwd") == (None, None)


def test_unique_basename_resolves(tmp_path: Path):
    cam = tmp_path / "CAM"
    (cam / "Project").mkdir(parents=True)
    body = b"T1\nG1 X1 F100\n"
    f = cam / "Project" / "part.gcode"
    f.write_bytes(body)
    result = resolve_cam_gcode("0:/gcodes/part.gcode", cam_dir=cam)
    assert result.status == "resolved"
    assert result.source_ref == "Project/part.gcode"
    assert result.gcode_summary is not None
    assert result.gcode_summary["tools_used"] == [1]
    assert result.gcode_sha256 == sha256_bytes(body)


def test_duplicate_basename_requires_hash(tmp_path: Path):
    cam = tmp_path / "CAM"
    (cam / "A").mkdir(parents=True)
    (cam / "B").mkdir(parents=True)
    a = b"T1\nG1 X1 F100\n"
    b = b"T2\nG1 X2 F200\n"
    (cam / "A" / "dup.gcode").write_bytes(a)
    (cam / "B" / "dup.gcode").write_bytes(b)
    amb = resolve_cam_gcode("0:/gcodes/dup.gcode", cam_dir=cam)
    assert amb.status == "ambiguous"
    assert amb.source_ref is None
    assert set(amb.candidates or []) == {"A/dup.gcode", "B/dup.gcode"}

    hit = resolve_cam_gcode(
        "0:/gcodes/dup.gcode",
        cam_dir=cam,
        gcode_sha256=sha256_bytes(b),
    )
    assert hit.status == "resolved"
    assert hit.source_ref == "B/dup.gcode"


def test_symlink_not_followed(tmp_path: Path):
    cam = tmp_path / "CAM"
    cam.mkdir()
    real = cam / "real.gcode"
    real.write_text("T1\n")
    link = cam / "gcodes"
    try:
        link.symlink_to("gcodes")  # broken self-ref like the share
    except OSError:
        return
    result = resolve_cam_gcode("0:/gcodes/real.gcode", cam_dir=cam)
    assert result.status == "resolved"
    assert result.source_ref == "real.gcode"


def test_summarize_preserves_byte_offsets():
    text = "T1\nG0 X0\nT2\n"
    out = summarize_gcode_text(text, job_file="x.gcode")
    assert out["tools_used"] == [1, 2]
    assert out["tool_change_lines"][0]["byte_offset"] == 0
    assert out["tool_change_lines"][1]["byte_offset"] > 0
