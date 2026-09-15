"""ffmpeg process supervisor for session.mp4 (+ optional YouTube)."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import threading
import time
from pathlib import Path

from loguru import logger

from ffmpeg_cmd import (
    build_concat_command,
    build_remux_command,
    build_stream_command,
    redact_cmd,
    session_mp4_path,
    session_part_path,
)
from hud import HudState
from utils.config import Settings

MAX_FFMPEG_RESTARTS = 5
RESTART_BACKOFF_S = 2.0


class StreamSupervisor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._session_id: str = ""
        self._overlay_path: Path | None = None
        self._output_mp4: Path | None = None
        self._staging_mp4: Path | None = None
        self._part_index: int = 0
        self._part_paths: list[Path] = []
        self._ffmpeg_restarts: int = 0
        self._started_at: float | None = None
        self._last_stderr: str = ""
        self._stderr_buf: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._stderr_stop = threading.Event()
        self._hud = HudState()
        self._overlay_thread: threading.Thread | None = None
        self._overlay_stop = threading.Event()
        self._intentional_stop = False
        self._restart_after_mono: float | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def started_at(self) -> float | None:
        return self._started_at

    @property
    def overlay_age_s(self) -> float | None:
        return self._hud.overlay_age_s()

    @property
    def last_stderr(self) -> str:
        return self._last_stderr

    @property
    def output_mp4(self) -> str | None:
        return str(self._output_mp4) if self._output_mp4 is not None else None

    @property
    def ffmpeg_restarts(self) -> int:
        return self._ffmpeg_restarts

    @property
    def part_count(self) -> int:
        return len(self._part_paths)

    def update_hud(self, topic_leaf: str, payload: dict) -> None:
        with self._lock:
            if topic_leaf == "video_overlay":
                self._hud.apply_video_overlay(payload)
            elif topic_leaf == "accel/batch":
                self._hud.apply_accel_batch(payload)

    def apply_settings(self, settings: Settings) -> None:
        """Replace settings for the next session (does not restart running ffmpeg)."""
        with self._lock:
            self.settings = settings

    def start_session(self, session_id: str) -> bool:
        with self._lock:
            if self.running:
                if self._session_id == session_id:
                    return True
                self._intentional_stop = True
                self._stop_unlocked(promote=True)
            return self._start_unlocked(session_id, reset_parts=True)

    def stop_session(self) -> None:
        with self._lock:
            self._intentional_stop = True
            self._stop_unlocked(promote=True)

    def _start_unlocked(self, session_id: str, *, reset_parts: bool) -> bool:
        yt_key = self.settings.effective_youtube_key
        out_mp4 = session_mp4_path(self.settings, session_id)
        if out_mp4 is None and not yt_key:
            logger.error(
                "neither TAP_STREAM_RECORDINGS_DIR nor enabled "
                "TAP_YOUTUBE_STREAM_KEY is set — stream not started"
            )
            return False
        work = self.settings.work_dir / session_id
        if reset_parts:
            self._part_index = 0
            self._part_paths = []
            self._ffmpeg_restarts = 0
        staging_mp4 = (
            session_part_path(self.settings.work_dir, session_id, self._part_index)
            if out_mp4 is not None
            else None
        )
        try:
            work.mkdir(parents=True, exist_ok=True)
            if out_mp4 is not None:
                out_mp4.parent.mkdir(parents=True, exist_ok=True)
            overlay_path = work / "overlay.txt"
            if reset_parts or not overlay_path.is_file():
                overlay_path.write_text(f"session {session_id}\n", encoding="utf-8")
        except OSError as e:
            logger.error("session work/output dirs failed: {}", e)
            return False
        try:
            cmd = build_stream_command(
                self.settings,
                overlay_path=overlay_path,
                session_id=session_id,
                output_mp4=staging_mp4,
            )
        except ValueError as e:
            logger.error("ffmpeg command build failed: {}", e)
            return False
        logger.info(
            "Starting tap-stream ffmpeg part={} staging={} final={} youtube={} restarts={}: {}",
            self._part_index,
            staging_mp4,
            out_mp4,
            bool(yt_key),
            self._ffmpeg_restarts,
            redact_cmd(cmd, yt_key),
        )
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            logger.error("ffmpeg failed to start: {}", e)
            return False
        self._proc = proc
        self._session_id = session_id
        self._overlay_path = overlay_path
        self._output_mp4 = out_mp4
        self._staging_mp4 = staging_mp4
        if staging_mp4 is not None and staging_mp4 not in self._part_paths:
            self._part_paths.append(staging_mp4)
        if reset_parts or self._started_at is None:
            self._started_at = time.monotonic()
        self._intentional_stop = False
        self._stderr_buf = []
        self._start_stderr_reader(proc)
        if reset_parts:
            self._hud.reset()
            self._hud.session_id = session_id
            self._overlay_stop.clear()
            self._overlay_thread = threading.Thread(
                target=self._overlay_loop,
                name=f"tap-stream-overlay-{session_id}",
                daemon=True,
            )
            self._overlay_thread.start()
        return True

    def _overlay_loop(self) -> None:
        interval = 0.2
        while not self._overlay_stop.wait(interval):
            with self._lock:
                path = self._overlay_path
                hud = self._hud
                stale = self.settings.overlay_stale_s
            if path is None:
                continue
            try:
                hud.write_overlay_file(path, overlay_stale_s=stale)
            except OSError as e:
                logger.debug("overlay write failed: {}", e)

    def _start_stderr_reader(self, proc: subprocess.Popen) -> None:
        self._stderr_stop.set()
        thr = self._stderr_thread
        if thr is not None and thr.is_alive():
            thr.join(timeout=0.5)
        self._stderr_stop.clear()

        def _read() -> None:
            if proc.stderr is None:
                return
            try:
                while not self._stderr_stop.is_set():
                    line = proc.stderr.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        self._stderr_buf.append(text)
                        if len(self._stderr_buf) > 400:
                            self._stderr_buf = self._stderr_buf[-200:]
            except (OSError, ValueError):
                return

        self._stderr_thread = threading.Thread(
            target=_read, name="tap-stream-ffmpeg-stderr", daemon=True
        )
        self._stderr_thread.start()

    def _finalize_stderr(self, proc: subprocess.Popen) -> str:
        self._stderr_stop.set()
        thr = self._stderr_thread
        if thr is not None and thr.is_alive():
            thr.join(timeout=1.0)
        self._stderr_thread = None
        leftover = self._drain_stderr(proc)
        if leftover:
            for line in leftover.splitlines():
                if line.strip():
                    self._stderr_buf.append(line.strip())
        joined = "\n".join(self._stderr_buf[-200:])
        self._last_stderr = joined
        return joined

    def _run_ffmpeg_promote(
        self, cmd: list[str], *, label: str, timeout: float
    ) -> bool:
        logger.info("{} ({})", label, " ".join(str(x) for x in cmd[:16]))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            logger.error(
                "{} failed code={} stderr={}",
                label,
                proc.returncode,
                (proc.stderr or "")[-2000:],
            )
            return False
        return True

    def _promote_parts_to_final(self) -> None:
        """Remux fragmented staging parts → seekable NFS session.mp4."""
        final = self._output_mp4
        if final is None:
            return
        parts = [p for p in self._part_paths if p.is_file() and p.stat().st_size > 0]
        if not parts:
            logger.warning("no staging parts to promote for {}", self._session_id)
            return
        timeout = max(60.0, self.settings.stop_timeout_s * 3)
        remux_temps: list[Path] = []
        tmp_final = final.with_suffix(".mp4.partial")
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            if tmp_final.is_file():
                tmp_final.unlink()

            if len(parts) == 1:
                cmd = build_remux_command(
                    self.settings, input_mp4=parts[0], output_mp4=tmp_final
                )
                logger.info("remux 1 part → {}", tmp_final)
                if not self._run_ffmpeg_promote(cmd, label="remux", timeout=timeout):
                    logger.error(
                        "promote aborted for {} — leaving staging parts at {}",
                        self._session_id,
                        parts[0].parent,
                    )
                    return
            else:
                # Remux each frag part to a seekable intermediate, then concat.
                fixed: list[Path] = []
                for i, part in enumerate(parts):
                    mid = part.with_name(f"{part.stem}.seekable.mp4")
                    remux_temps.append(mid)
                    cmd = build_remux_command(
                        self.settings, input_mp4=part, output_mp4=mid
                    )
                    logger.info("remux part {}/{} → {}", i + 1, len(parts), mid)
                    if not self._run_ffmpeg_promote(
                        cmd, label=f"remux-part-{i}", timeout=timeout
                    ):
                        logger.error(
                            "promote aborted for {} — leaving staging parts at {}",
                            self._session_id,
                            part.parent,
                        )
                        return
                    if not mid.is_file() or mid.stat().st_size <= 0:
                        logger.error("remux produced empty file {}", mid)
                        return
                    fixed.append(mid)
                cmd = build_concat_command(
                    self.settings, part_paths=fixed, output_mp4=tmp_final
                )
                logger.info("concat {} remuxed parts → {}", len(fixed), tmp_final)
                if not self._run_ffmpeg_promote(cmd, label="concat", timeout=timeout):
                    logger.error(
                        "promote aborted for {} — leaving staging parts at {}",
                        self._session_id,
                        parts[0].parent,
                    )
                    return

            if not tmp_final.is_file() or tmp_final.stat().st_size <= 0:
                logger.error("promote produced empty {}", tmp_final)
                return

            tmp_final.replace(final)
            logger.info(
                "promoted {} part(s) → {} ({} bytes)",
                len(parts),
                final,
                final.stat().st_size if final.is_file() else 0,
            )
            for p in parts:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            for p in remux_temps:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            for sibling in (
                final.with_suffix(".concat.txt"),
                tmp_final.with_suffix(".concat.txt"),
            ):
                try:
                    sibling.unlink(missing_ok=True)
                except OSError:
                    pass
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("failed to promote session parts to {}: {}", final, e)
            for p in remux_temps:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                tmp_final.unlink(missing_ok=True)
            except OSError:
                pass

    def _stop_proc_only(self) -> int | None:
        """Stop current ffmpeg; keep session/overlay state. Returns exit code."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return None
        still_running = proc.poll() is None
        if proc.stdin is not None:
            try:
                if still_running:
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        if still_running:
            try:
                proc.send_signal(signal.SIGINT)
            except OSError:
                pass
            try:
                proc.wait(timeout=self.settings.stop_timeout_s + 5.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5.0)
        stderr = self._finalize_stderr(proc)
        if stderr:
            logger.warning("ffmpeg stderr:\n{}", stderr[-4000:])
        code = proc.returncode
        if code not in (0, None, 255, -1):
            logger.error("ffmpeg exited with code {}", code)
        elif code not in (0, None):
            logger.info("ffmpeg exited with code {} (teardown)", code)
        return code

    def _stop_unlocked(self, *, promote: bool) -> None:
        self._overlay_stop.set()
        thr = self._overlay_thread
        if thr is not None and thr.is_alive() and thr is not threading.current_thread():
            thr.join(timeout=1.0)
        self._overlay_thread = None
        self._stop_proc_only()
        if promote:
            self._promote_parts_to_final()
            if self._output_mp4 is not None:
                logger.info("session recording path: {}", self._output_mp4)
        self._session_id = ""
        self._overlay_path = None
        self._output_mp4 = None
        self._staging_mp4 = None
        self._part_index = 0
        self._part_paths = []
        self._started_at = None
        self._hud.reset()
        self._intentional_stop = False

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen) -> str:
        if proc.stderr is None:
            return ""
        try:
            if proc.poll() is None:
                fd = proc.stderr.fileno()
                end = time.monotonic() + 2.0
                chunks: list[bytes] = []
                while time.monotonic() < end:
                    remaining = end - time.monotonic()
                    r, _, _ = select.select([fd], [], [], remaining)
                    if not r:
                        break
                    data = os.read(fd, 4096)
                    if not data:
                        break
                    chunks.append(data)
                raw = b"".join(chunks)
            else:
                raw = proc.stderr.read() or b""
        except OSError:
            return ""
        return raw.decode("utf-8", errors="replace").strip()

    def watchdog(self) -> None:
        """Restart or stop ffmpeg if process died while session still active.

        Non-blocking: uses a deferred restart timestamp so the MQTT asyncio
        loop is not stalled by sleep().
        """
        with self._lock:
            # Deferred restart after backoff
            if (
                self._proc is None
                and self._session_id
                and self._restart_after_mono is not None
                and not self._intentional_stop
            ):
                if time.monotonic() < self._restart_after_mono:
                    return
                sid = self._session_id
                self._restart_after_mono = None
                ok = self._start_unlocked(sid, reset_parts=False)
                if not ok:
                    logger.error(
                        "ffmpeg restart failed for {} — promoting partial parts", sid
                    )
                    self._stop_unlocked(promote=True)
                return

            proc = self._proc
            sid = self._session_id
            intentional = self._intentional_stop
            if proc is None or not sid:
                return
            code = proc.poll()
            if code is None:
                return
            stderr = self._finalize_stderr(proc)
            if stderr:
                logger.warning("ffmpeg stderr (unexpected death):\n{}", stderr[-4000:])
            self._proc = None
            logger.error(
                "ffmpeg died unexpectedly during session {} (code={}) restarts={}/{}",
                sid,
                code,
                self._ffmpeg_restarts,
                MAX_FFMPEG_RESTARTS,
            )
            if intentional:
                self._stop_unlocked(promote=True)
                return
            if self._ffmpeg_restarts >= MAX_FFMPEG_RESTARTS:
                logger.error(
                    "ffmpeg restart budget exhausted for {} — promoting {} part(s)",
                    sid,
                    len(self._part_paths),
                )
                self._stop_unlocked(promote=True)
                return
            self._ffmpeg_restarts += 1
            self._part_index += 1
            backoff = RESTART_BACKOFF_S * self._ffmpeg_restarts
            self._restart_after_mono = time.monotonic() + backoff
            logger.info(
                "scheduling ffmpeg restart for {} in {:.1f}s (part {})",
                sid,
                backoff,
                self._part_index,
            )
