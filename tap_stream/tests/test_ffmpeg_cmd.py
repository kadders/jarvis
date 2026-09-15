"""Tests for ffmpeg command builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ffmpeg_cmd import build_stream_command, build_youtube_stream_command
from supervisor import StreamSupervisor
from utils.config import Settings


def _base_settings(**kwargs) -> Settings:
    defaults = dict(
        mjpeg_url="http://192.168.86.65:8081/stream",
        youtube_stream_key="",
        rotate_deg=90,
        max_width=720,
        fontfile="/nonexistent/font.ttf",
        recordings_dir=None,
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def test_build_stream_command_default_fps_15(tmp_path: Path):
    overlay = tmp_path / "overlay.txt"
    overlay.write_text("hello", encoding="utf-8")
    recordings = tmp_path / "recordings"
    settings = _base_settings(recordings_dir=recordings)
    assert settings.fps == 15.0
    cmd = build_stream_command(settings, overlay_path=overlay, session_id="fps-test")
    assert cmd[cmd.index("-r") + 1] == "15"


def test_build_youtube_stream_command(tmp_path: Path):
    overlay = tmp_path / "overlay.txt"
    overlay.write_text("hello", encoding="utf-8")
    settings = _base_settings(youtube_stream_key="test-key", youtube_enabled=True)
    cmd = build_youtube_stream_command(settings, overlay_path=overlay, session_id="sess1")
    joined = " ".join(cmd)
    assert "http://192.168.86.65:8081/stream" in cmd
    assert "transpose=1" in joined
    assert "scale=720:-2" in joined
    assert "drawtext=" in joined
    assert "libx264" in cmd
    assert "-f fifo" in joined
    assert "test-key" in joined


def test_youtube_disabled_ignores_stream_key(tmp_path: Path):
    overlay = tmp_path / "overlay.txt"
    overlay.write_text("hello", encoding="utf-8")
    recordings = tmp_path / "recordings"
    settings = _base_settings(
        youtube_stream_key="test-key",
        youtube_enabled=False,
        recordings_dir=recordings,
    )
    cmd = build_stream_command(settings, overlay_path=overlay, session_id="sess1")
    joined = " ".join(cmd)
    assert "test-key" not in joined
    assert "youtube.com" not in joined
    assert joined.count("libx264") == 1


def test_build_local_only_session_mp4(tmp_path: Path):
    overlay = tmp_path / "overlay.txt"
    overlay.write_text("hello", encoding="utf-8")
    recordings = tmp_path / "recordings"
    settings = _base_settings(recordings_dir=recordings)
    cmd = build_stream_command(settings, overlay_path=overlay, session_id="abc123")
    joined = " ".join(cmd)
    assert str(recordings / "abc123" / "session.mp4") in cmd
    assert "frag_keyframe+empty_moov+default_base_moof" in joined
    assert "youtube.com" not in joined
    assert "-f tee" not in joined


def test_build_dual_mp4_and_youtube(tmp_path: Path):
    overlay = tmp_path / "overlay.txt"
    overlay.write_text("hello", encoding="utf-8")
    recordings = tmp_path / "recordings"
    settings = _base_settings(
        youtube_stream_key="test-key",
        youtube_enabled=True,
        recordings_dir=recordings,
    )
    cmd = build_stream_command(settings, overlay_path=overlay, session_id="sess1")
    joined = " ".join(cmd)
    assert "-f tee" not in joined
    assert str(recordings / "sess1" / "session.mp4") in cmd
    assert "test-key" in joined
    assert joined.count("libx264") == 2
    assert "-f fifo" in joined


def test_start_session_without_youtube_when_recordings_set(tmp_path: Path):
    recordings = tmp_path / "recordings"
    work = tmp_path / "work"
    settings = _base_settings(
        work_dir=work,
        recordings_dir=recordings,
        youtube_stream_key="",
    )
    supervisor = StreamSupervisor(settings)
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = MagicMock()
    fake_proc.stderr = None
    with patch("supervisor.subprocess.Popen", return_value=fake_proc) as popen:
        assert supervisor.start_session("job-1") is True
        popen.assert_called_once()
        cmd = popen.call_args[0][0]
        # Encode stages locally as session_part000; NFS path is the final promote target.
        assert str(work / "job-1" / "session_part000.mp4") in cmd
        assert str(recordings / "job-1" / "session.mp4") not in cmd
    assert (recordings / "job-1").is_dir()
    assert supervisor.output_mp4 == str(recordings / "job-1" / "session.mp4")


def test_thread_queue_size_hardened(tmp_path: Path):
    overlay = tmp_path / "overlay.txt"
    overlay.write_text("hello", encoding="utf-8")
    recordings = tmp_path / "recordings"
    settings = _base_settings(recordings_dir=recordings)
    cmd = build_stream_command(settings, overlay_path=overlay, session_id="q")
    assert cmd[cmd.index("-thread_queue_size") + 1] == "512"


def test_session_part_path_and_concat_list(tmp_path: Path):
    from ffmpeg_cmd import build_concat_command, session_part_path

    p0 = session_part_path(tmp_path, "sid", 0)
    p1 = session_part_path(tmp_path, "sid", 1)
    assert p0.name == "session_part000.mp4"
    assert p1.name == "session_part001.mp4"
    p0.parent.mkdir(parents=True)
    p0.write_bytes(b"x")
    p1.write_bytes(b"y")
    out = tmp_path / "out.mp4"
    settings = _base_settings()
    cmd = build_concat_command(settings, part_paths=[p0, p1], output_mp4=out)
    assert "-f" in cmd and "concat" in cmd
    assert "-c" in cmd and "copy" in cmd
    assert "+faststart" in " ".join(cmd)
    assert (out.with_suffix(".concat.txt")).is_file()


def test_build_remux_command(tmp_path: Path):
    from ffmpeg_cmd import build_remux_command

    src = tmp_path / "frag.mp4"
    dst = tmp_path / "out.mp4"
    src.write_bytes(b"x")
    settings = _base_settings()
    cmd = build_remux_command(settings, input_mp4=src, output_mp4=dst)
    joined = " ".join(cmd)
    assert str(src) in cmd
    assert str(dst) in cmd
    assert "-c" in cmd and "copy" in cmd
    assert "+faststart" in joined
    assert "frag_keyframe" not in joined


def test_promote_single_part_remuxes(tmp_path: Path):
    recordings = tmp_path / "recordings"
    work = tmp_path / "work"
    settings = _base_settings(work_dir=work, recordings_dir=recordings)
    supervisor = StreamSupervisor(settings)
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = MagicMock()
    fake_proc.stderr = None
    with patch("supervisor.subprocess.Popen", return_value=fake_proc):
        assert supervisor.start_session("job-p") is True
    part = work / "job-p" / "session_part000.mp4"
    part.write_bytes(b"frag-data")

    remux_cmds: list[list] = []

    def fake_run(cmd, **kwargs):
        remux_cmds.append(list(cmd))
        # Write the promote partial so replace succeeds
        out = Path(cmd[-1])
        out.write_bytes(b"seekable")
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        return m

    with patch("supervisor.subprocess.run", side_effect=fake_run):
        supervisor.stop_session()

    assert len(remux_cmds) == 1
    joined = " ".join(str(x) for x in remux_cmds[0])
    assert "+faststart" in joined
    assert "copy" in remux_cmds[0]
    final = recordings / "job-p" / "session.mp4"
    assert final.is_file()
    assert final.read_bytes() == b"seekable"
    assert not part.exists()


def test_promote_failure_leaves_staging_parts(tmp_path: Path):
    recordings = tmp_path / "recordings"
    work = tmp_path / "work"
    settings = _base_settings(work_dir=work, recordings_dir=recordings)
    supervisor = StreamSupervisor(settings)
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = MagicMock()
    fake_proc.stderr = None
    with patch("supervisor.subprocess.Popen", return_value=fake_proc):
        assert supervisor.start_session("job-fail") is True
    part = work / "job-fail" / "session_part000.mp4"
    part.write_bytes(b"frag-data")

    def fake_run(cmd, **kwargs):
        m = MagicMock()
        m.returncode = 1
        m.stderr = "remux boom"
        return m

    with patch("supervisor.subprocess.run", side_effect=fake_run):
        supervisor.stop_session()

    assert part.is_file()
    assert not (recordings / "job-fail" / "session.mp4").is_file()


def test_watchdog_schedules_restart_on_death(tmp_path: Path):
    recordings = tmp_path / "recordings"
    work = tmp_path / "work"
    settings = _base_settings(work_dir=work, recordings_dir=recordings)
    supervisor = StreamSupervisor(settings)
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = MagicMock()
    fake_proc.stderr = None
    with patch("supervisor.subprocess.Popen", return_value=fake_proc):
        assert supervisor.start_session("job-r") is True
    # Simulate unexpected death
    fake_proc.poll.return_value = -7
    fake_proc.returncode = -7
    supervisor.watchdog()
    assert supervisor.session_id == "job-r"
    assert supervisor.ffmpeg_restarts == 1
    assert supervisor._part_index == 1
    assert supervisor._restart_after_mono is not None
    # Force due restart
    supervisor._restart_after_mono = 0.0
    fake2 = MagicMock()
    fake2.poll.return_value = None
    fake2.stdin = MagicMock()
    fake2.stderr = None
    with patch("supervisor.subprocess.Popen", return_value=fake2) as popen:
        supervisor.watchdog()
        popen.assert_called_once()
        cmd = popen.call_args[0][0]
        assert "session_part001.mp4" in " ".join(str(x) for x in cmd)
    assert supervisor.running
    assert supervisor.part_count == 2


def test_start_session_fails_without_outputs(tmp_path: Path):
    settings = _base_settings(work_dir=tmp_path / "work")
    supervisor = StreamSupervisor(settings)
    assert supervisor.start_session("job-1") is False
