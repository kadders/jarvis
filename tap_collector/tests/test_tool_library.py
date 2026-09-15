"""Tests for Fusion tool library import."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from models.tool_library import ToolLibraryStore, extract_fusion_tools_bytes, normalize_fusion_tool


def _sample_tools():
    return [
        {
            "guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "description": "1/4 EM 4FL",
            "type": "flat end mill",
            "vendor": "Harvey",
            "product-id": "12345",
            "BMC": "carbide",
            "geometry": {"DC": 0.25, "NOF": 4},
            "holder": {"description": "CAT40-ER32"},
            "post-process": {"number": 3},
        },
        {
            "guid": "11111111-2222-3333-4444-555555555555",
            "description": "1/8 ball",
            "type": "ball end mill",
            "vendor": "Harvey",
            "product-id": "67890",
            "geometry": {"DC": 0.125, "NOF": 2},
            "post-process": {"number": 5},
        },
    ]


def test_normalize_fusion_tool():
    rec = normalize_fusion_tool(_sample_tools()[0])
    assert rec["guid"].startswith("aaaa")
    assert rec["diameter"] == 0.25
    assert rec["flutes"] == 4
    assert rec["tool_number"] == 3
    assert rec["holder"] == "CAT40-ER32"
    assert "fusion" in rec
    assert rec.get("diameter_mm") is not None


def test_normalize_fusion_start_values_rpm():
    raw = dict(_sample_tools()[0])
    raw["start-values"] = {"spindleSpeed": 16500}
    rec = normalize_fusion_tool(raw)
    assert rec["reference_rpm"] == 16500
    assert rec["reference_rpm_source"] == "fusion_reference"


def test_extract_json_and_tools_zip(tmp_path: Path):
    payload = {"data": _sample_tools()}
    raw_json = json.dumps(payload).encode()
    tools, label = extract_fusion_tools_bytes(raw_json, filename="lib.json")
    assert len(tools) == 2
    assert label == "lib.json"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("tools.json", json.dumps(payload))
    tools2, label2 = extract_fusion_tools_bytes(buf.getvalue(), filename="mine.tools")
    assert len(tools2) == 2
    assert "tools.json" in label2


def test_catalog_upsert(tmp_path: Path):
    store = ToolLibraryStore(tmp_path / "sessions")
    raw = json.dumps({"data": _sample_tools()}).encode()
    r1 = store.import_bytes(raw, filename="lib.json")
    assert r1["created"] == 2
    assert r1["updated"] == 0
    assert r1["n_tools"] == 2
    assert r1["mode"] == "upsert"
    assert r1["replaced_duplicates"] == 0

    # Second import updates same GUIDs
    r2 = store.import_bytes(raw, filename="lib.json")
    assert r2["created"] == 0
    assert r2["updated"] == 2
    assert store.summary()["n_tools"] == 2

    listed = store.list_tools()
    assert len(listed) == 2
    assert "fusion" not in listed[0]

    by_num = store.lookup_by_number(3)
    assert len(by_num) == 1
    assert by_num[0]["description"] == "1/4 EM 4FL"

    one = store.get_tool("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert one is not None
    assert one["fusion"]["vendor"] == "Harvey"

    searched = store.list_tools(q="ball")
    assert len(searched) == 1
    assert searched[0]["tool_number"] == 5


def test_import_evicts_duplicate_tool_number(tmp_path: Path):
    store = ToolLibraryStore(tmp_path / "sessions")
    first = [
        {
            "guid": "old-guid-number-3",
            "description": "old T3",
            "geometry": {"DC": 0.25, "NOF": 2},
            "post-process": {"number": 3},
        }
    ]
    store.import_bytes(json.dumps({"data": first}).encode(), filename="old.json")
    assert store.lookup_by_number(3)[0]["guid"] == "old-guid-number-3"

    newer = [
        {
            "guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "description": "new T3",
            "geometry": {"DC": 0.25, "NOF": 4},
            "post-process": {"number": 3},
        }
    ]
    r = store.import_bytes(json.dumps({"data": newer}).encode(), filename="new.json")
    assert r["replaced_duplicates"] == 1
    assert r["removed"] == 1
    assert store.summary()["n_tools"] == 1
    assert store.get_tool("old-guid-number-3") is None
    assert store.lookup_by_number(3)[0]["guid"].startswith("aaaa")


def test_import_replace_entire_catalog(tmp_path: Path):
    store = ToolLibraryStore(tmp_path / "sessions")
    store.import_bytes(json.dumps({"data": _sample_tools()}).encode(), filename="a.json")
    assert store.summary()["n_tools"] == 2

    only = [
        {
            "guid": "cccccccc-dddd-eeee-ffff-000000000000",
            "description": "solo",
            "geometry": {"DC": 0.5, "NOF": 3},
            "post-process": {"number": 9},
        }
    ]
    r = store.import_bytes(
        json.dumps({"data": only}).encode(), filename="b.json", replace=True
    )
    assert r["mode"] == "replace"
    assert r["removed"] == 2
    assert r["created"] == 1
    assert r["n_tools"] == 1
    assert store.get_tool("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") is None
    assert store.lookup_by_number(9)[0]["description"] == "solo"


def test_delete_and_patch_tool(tmp_path: Path):
    store = ToolLibraryStore(tmp_path / "sessions")
    store.import_bytes(json.dumps({"data": _sample_tools()}).encode(), filename="lib.json")
    guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    patched = store.patch_tool(guid, {"description": "renamed", "tool_number": 5})
    assert patched["description"] == "renamed"
    assert patched["tool_number"] == 5
    assert patched["replaced_duplicates"] == 1  # evicted previous T5
    assert store.get_tool("11111111-2222-3333-4444-555555555555") is None

    deleted = store.delete_tool(guid)
    assert deleted["deleted"] is True
    assert store.get_tool(guid) is None

    assert (tmp_path / "sessions" / "tool_library" / "catalog.json").is_file()
    assert list((tmp_path / "sessions" / "tool_library" / "originals").glob("*"))
