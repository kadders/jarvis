"""Configure loguru once at process startup."""
from __future__ import annotations

import sys

from loguru import logger

from utils.config import get_settings
from utils.request_context import get_request_id


def _patcher(record: dict) -> None:
    rid = get_request_id()
    record["extra"]["request_id"] = rid if rid else "-"


def configure_logging() -> None:
    settings = get_settings()
    logger.remove()
    logger.configure(patcher=_patcher)
    if settings.log_json:
        logger.add(
            sys.stderr,
            level=settings.log_level,
            serialize=True,
        )
    else:
        fmt = (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[request_id]}</cyan> | "
            "{message}"
        )
        logger.add(sys.stderr, level=settings.log_level, format=fmt)
