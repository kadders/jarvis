from __future__ import annotations

import json
import time

from utils.ocr.image_store import ImageStore


def test_sweep_expired_removes_stale_meta(tmp_path) -> None:
    store = ImageStore(str(tmp_path), ttl_seconds=3600)
    s = store.save(b"hello", "image/png")
    meta = json.loads(s.meta_path.read_text(encoding="utf-8"))
    meta["expires_at"] = time.time() - 5.0
    s.meta_path.write_text(json.dumps(meta), encoding="utf-8")
    removed = store.sweep_expired(batch=50)
    assert removed >= 1
    assert not s.path.is_file()
    assert not s.meta_path.is_file()
