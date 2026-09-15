"""
Shared Kubernetes-style ConfigMap volume polling.

Mount a ConfigMap as a read-only directory; each key becomes a filename. Poll
for mtime changes, update os.environ, clear settings cache, then rebind.

Copy this file into other service images that use a separate Docker context
(ASR, TTS) so they stay in sync with the LLM API pattern.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, Union

from loguru import logger

_ENV_FILE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

RebindFn = Union[
    Callable[[Any], None],
    Callable[[Any], Awaitable[None]],
]


def scan_config_dir(dir_path: Path) -> Dict[str, Tuple[float, str]]:
    """Map env key -> (mtime, value) for eligible files under dir_path."""
    out: Dict[str, Tuple[float, str]] = {}
    if not dir_path.is_dir():
        return out
    try:
        for f in sorted(dir_path.iterdir()):
            if not f.is_file():
                continue
            name = f.name
            if not _ENV_FILE_KEY.match(name):
                continue
            try:
                st = f.stat()
                raw = f.read_text(encoding="utf-8")
            except OSError:
                continue
            val = raw.rstrip("\r\n")
            out[name] = (st.st_mtime, val)
    except OSError as e:
        logger.warning(f"config watch: cannot read {dir_path}: {e}")
    return out


async def _invoke_rebind(app: Any, fn: RebindFn) -> None:
    if inspect.iscoroutinefunction(fn):
        await fn(app)
    else:
        fn(app)


async def run_mounted_config_poll_loop(
    app: Any,
    *,
    get_settings: Callable[[], Any],
    clear_settings_cache: Callable[[], None],
    on_rebind: RebindFn,
    reload_lock_attr: str = "config_reload_lock",
    config_dir_attr: str = "jarvis_config_dir",
    interval_attr: str = "jarvis_config_reload_interval_s",
    min_interval_s: float = 5.0,
    log_prefix: str = "config",
    logging_env_keys: frozenset[str] = frozenset({"LOG_LEVEL", "LOG_JSON"}),
    configure_logging_fn: Optional[Callable[[], None]] = None,
) -> None:
    """
    Poll ``getattr(settings, config_dir_attr)`` for file updates.

    When ``configure_logging_fn`` is set and a changed key is in
    ``logging_env_keys``, it is called after ``clear_settings_cache`` and
    before ``on_rebind``.
    """
    cfg0 = get_settings()
    watch = (getattr(cfg0, config_dir_attr, None) or "").strip()
    if not watch:
        return
    path = Path(watch)
    if not path.is_dir():
        logger.warning(
            f"[{log_prefix}] JARVIS_CONFIG_DIR={path} is not a directory; "
            "hot-reload task not started"
        )
        return
    last_mt: Dict[str, float] = {k: v[0] for k, v in scan_config_dir(path).items()}
    interval0 = float(getattr(cfg0, interval_attr, min_interval_s))
    logger.info(
        f"[{log_prefix}] config hot-reload enabled dir={path} interval_s={interval0}"
    )

    while True:
        cfg = get_settings()
        watch = (getattr(cfg, config_dir_attr, None) or "").strip()
        if not watch:
            logger.info(f"[{log_prefix}] hot-reload disabled (JARVIS_CONFIG_DIR cleared)")
            return
        path = Path(watch)
        await asyncio.sleep(max(min_interval_s, float(getattr(cfg, interval_attr, min_interval_s))))
        if not path.is_dir():
            continue

        snap = scan_config_dir(path)
        changed: list[str] = []
        for name, (mt, content) in snap.items():
            if last_mt.get(name) != mt:
                os.environ[name] = content
                last_mt[name] = mt
                changed.append(name)

        if not changed:
            continue

        clear_settings_cache()
        if configure_logging_fn and logging_env_keys.intersection(changed):
            configure_logging_fn()

        lock = getattr(app.state, reload_lock_attr, None)
        if lock is None:
            logger.warning(f"[{log_prefix}] missing {reload_lock_attr} on app.state")
            continue
        async with lock:
            await _invoke_rebind(app, on_rebind)

        logger.info(f"[{log_prefix}] config hot-reload applied keys={changed}")
