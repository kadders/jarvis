from __future__ import annotations

from pathlib import Path

from utils.k8s_config_reload import scan_config_dir


def test_scan_config_dir_reads_env_like_files(tmp_path: Path) -> None:
    (tmp_path / "MODEL_NAME").write_text("my-model\n", encoding="utf-8")
    (tmp_path / "SKIP.dotfile").write_text("x", encoding="utf-8")
    (tmp_path / "bad-name").write_text("y", encoding="utf-8")
    snap = scan_config_dir(tmp_path)
    assert "MODEL_NAME" in snap
    assert snap["MODEL_NAME"][1] == "my-model"
    assert "SKIP.dotfile" not in snap
    assert "bad-name" not in snap
