"""Tests for file-based settings load and fingerprint."""

from __future__ import annotations

from pathlib import Path

from utils.config import load_settings, settings_fingerprint


def test_load_from_config_dir(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "TAP_VIDEO_FPS").write_text("30\n", encoding="utf-8")
    (cfg / "TAP_YOUTUBE_ENABLED").write_text("false\n", encoding="utf-8")
    (cfg / "TAP_STREAM_DEVICE_ID").write_text("milo\n", encoding="utf-8")
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "TAP_YOUTUBE_STREAM_KEY").write_text("secret-key\n", encoding="utf-8")

    s = load_settings(config_dir=cfg, secrets_dir=sec, environ={})
    assert s.fps == 30.0
    assert s.youtube_enabled is False
    assert s.youtube_stream_key == "secret-key"
    assert s.effective_youtube_key == ""
    assert s.youtube_configured is False
    assert s.config_fingerprint


def test_youtube_enabled_exposes_key(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "TAP_YOUTUBE_ENABLED").write_text("true\n", encoding="utf-8")
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "TAP_YOUTUBE_STREAM_KEY").write_text("secret-key\n", encoding="utf-8")
    s = load_settings(config_dir=cfg, secrets_dir=sec, environ={})
    assert s.effective_youtube_key == "secret-key"
    assert s.youtube_configured is True


def test_env_overrides_files(tmp_path: Path):
    """Env only fills gaps — mounted files win when both set (k8s-safe)."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "TAP_VIDEO_FPS").write_text("15\n", encoding="utf-8")
    s = load_settings(
        config_dir=cfg,
        secrets_dir=None,
        environ={"TAP_VIDEO_FPS": "30"},
    )
    assert s.fps == 15.0


def test_env_used_when_no_file(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    s = load_settings(
        config_dir=cfg,
        secrets_dir=None,
        environ={"TAP_VIDEO_FPS": "30"},
    )
    assert s.fps == 30.0


def test_k8s_service_port_env_ignored_when_file_present(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "TAP_STREAM_PORT").write_text("8005\n", encoding="utf-8")
    s = load_settings(
        config_dir=cfg,
        secrets_dir=None,
        environ={"TAP_STREAM_PORT": "tcp://10.152.183.117:8005"},
    )
    assert s.api_port == 8005


def test_fingerprint_stable_ignores_key_value(tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "TAP_YOUTUBE_ENABLED").write_text("true\n", encoding="utf-8")
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "TAP_YOUTUBE_STREAM_KEY").write_text("aaa\n", encoding="utf-8")
    a = load_settings(config_dir=cfg, secrets_dir=sec, environ={})
    (sec / "TAP_YOUTUBE_STREAM_KEY").write_text("bbb\n", encoding="utf-8")
    b = load_settings(config_dir=cfg, secrets_dir=sec, environ={})
    # Key value change still flips presence-stable fingerprint only if presence same —
    # both present → same fingerprint (by design: key_present bit only).
    assert settings_fingerprint(a) == settings_fingerprint(b)
