"""Build ffmpeg argv for cluster session.mp4 (+ optional YouTube tee)."""

from __future__ import annotations

from pathlib import Path

from utils.config import Settings

# Larger queue reduces MJPEG stall / SIGBUS risk under drawtext + CFR encode.
DEFAULT_THREAD_QUEUE_SIZE = 512


def _ffmpeg_escape_filter_path(path: Path | str) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")


def orientation_filters(rotate_deg: int, flip: str) -> list[str]:
    parts: list[str] = []
    deg = int(rotate_deg) % 360
    if deg == 90:
        parts.append("transpose=1")
    elif deg == 180:
        parts.append("transpose=1,transpose=1")
    elif deg == 270:
        parts.append("transpose=2")
    mode = (flip or "").strip().lower()
    if mode in ("h", "hv"):
        parts.append("hflip")
    if mode in ("v", "hv"):
        parts.append("vflip")
    return parts


def session_mp4_path(settings: Settings, session_id: str) -> Path | None:
    """Return ``{recordings_dir}/{session_id}/session.mp4`` when recordings are enabled."""
    root = settings.recordings_dir
    if root is None:
        return None
    return Path(root) / session_id / "session.mp4"


def session_part_path(work_dir: Path, session_id: str, part_index: int) -> Path:
    """Staging segment path: ``{work}/{session_id}/session_part{NNN}.mp4``."""
    return Path(work_dir) / session_id / f"session_part{int(part_index):03d}.mp4"


def build_stream_command(
    settings: Settings,
    *,
    overlay_path: Path,
    session_id: str,
    output_mp4: Path | None = None,
) -> list[str]:
    """Build ffmpeg argv for local MP4 and/or YouTube RTMP.

    - Local file: fragmented MP4 under ``TAP_STREAM_RECORDINGS_DIR``.
    - YouTube: optional fifo RTMP when ``TAP_YOUTUBE_STREAM_KEY`` is set.
    - Both: independent dual outputs (MP4 + fifo RTMP), not tee — RTMP must not corrupt MP4.
    """
    fps = max(1, int(settings.fps))
    gop = str(fps)
    max_w = max(320, min(int(settings.max_width), 1920))
    yt_key = settings.effective_youtube_key
    youtube = bool(yt_key)
    mp4 = output_mp4 if output_mp4 is not None else session_mp4_path(settings, session_id)
    if mp4 is None and not youtube:
        raise ValueError("neither recordings_dir nor TAP_YOUTUBE_STREAM_KEY is configured")

    font = settings.fontfile if Path(settings.fontfile).is_file() else ""
    escaped = _ffmpeg_escape_filter_path(overlay_path)
    draw = (
        f"drawtext=textfile={escaped}:reload=1:expansion=none"
        f":fontsize=18:fontcolor=white:box=1:boxcolor=black@0.55:x=10:y=10"
    )
    if font:
        draw += f":fontfile={_ffmpeg_escape_filter_path(font)}"

    vf_parts = ["scale=iw:ih:in_range=full:out_range=tv,format=yuv420p"]
    vf_parts.extend(orientation_filters(settings.rotate_deg, settings.flip))
    vf_parts.append(f"scale={max_w}:-2:flags=fast_bilinear,format=yuv420p")
    vf_parts.append(draw)

    queue = str(DEFAULT_THREAD_QUEUE_SIZE)
    cmd: list[str] = [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts+discardcorrupt",
        "-flags",
        "low_delay",
        "-use_wallclock_as_timestamps",
        "1",
        "-thread_queue_size",
        queue,
        "-f",
        "mjpeg",
        "-i",
        settings.mjpeg_url,
    ]
    if youtube:
        cmd.extend(
            [
                "-f",
                "lavfi",
                "-thread_queue_size",
                "8",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]
        )
    cmd.extend(
        [
            "-err_detect",
            "ignore_err",
            "-ignore_io_errors",
            "1",
            "-vf",
            ",".join(vf_parts),
        ]
    )

    v_encode = [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-bf",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        settings.bitrate,
        "-maxrate",
        settings.bitrate,
        "-bufsize",
        "4000k",
        "-g",
        gop,
        "-fps_mode",
        "cfr",
        "-r",
        str(fps),
    ]
    a_encode = [
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-b:a",
        "96k",
    ]
    fifo_rtmp = [
        "-f",
        "fifo",
        "-fifo_format",
        "flv",
        "-queue_size",
        "128",
        "-drop_pkts_on_overflow",
        "1",
        "-attempt_recovery",
        "1",
        "-recovery_wait_time",
        "1",
        "-restart_with_keyframe",
        "1",
        "-max_recovery_attempts",
        "5",
    ]

    if youtube and mp4 is not None:
        # Independent outputs (not tee): RTMP stalls must not corrupt session.mp4.
        dest = f"{settings.youtube_rtmp_url}/{yt_key}"
        cmd.extend(["-map", "0:v:0", *v_encode, "-an"])
        cmd.extend(
            [
                "-movflags",
                "frag_keyframe+empty_moov+default_base_moof",
                "-y",
                str(mp4),
            ]
        )
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0", *v_encode, *a_encode])
        cmd.extend([*fifo_rtmp, "-y", dest])
    elif youtube:
        dest = f"{settings.youtube_rtmp_url}/{yt_key}"
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0", *v_encode, *a_encode])
        cmd.extend([*fifo_rtmp, "-y", dest])
    else:
        assert mp4 is not None
        cmd.extend(["-map", "0:v:0", *v_encode, "-an"])
        cmd.extend(
            [
                "-movflags",
                "frag_keyframe+empty_moov+default_base_moof",
                "-y",
                str(mp4),
            ]
        )
    return cmd


def build_remux_command(
    settings: Settings,
    *,
    input_mp4: Path,
    output_mp4: Path,
) -> list[str]:
    """Remux fragmented staging MP4 → seekable faststart MP4 (stream copy)."""
    return [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(input_mp4),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        str(output_mp4),
    ]


def build_concat_command(
    settings: Settings,
    *,
    part_paths: list[Path],
    output_mp4: Path,
) -> list[str]:
    """Concat demuxer: copy already-remuxed (non-frag) segments into one MP4."""
    if not part_paths:
        raise ValueError("no part paths to concat")
    list_file = output_mp4.with_suffix(".concat.txt")
    lines = []
    for p in part_paths:
        # Single quotes escaped for concat demuxer
        escaped = str(p.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        str(output_mp4),
    ]


def build_youtube_stream_command(
    settings: Settings,
    *,
    overlay_path: Path,
    session_id: str,
) -> list[str]:
    """Backward-compatible alias for YouTube-oriented builds (may also write MP4)."""
    return build_stream_command(
        settings,
        overlay_path=overlay_path,
        session_id=session_id,
    )


def redact_cmd(cmd: list[str], stream_key: str) -> str:
    text = " ".join(cmd)
    key = (stream_key or "").strip()
    if key:
        text = text.replace(key, f"****{key[-4:]}" if len(key) > 4 else "****")
    return text
