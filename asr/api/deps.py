from __future__ import annotations

from fastapi import Request

from models.asr_handler import ASRHandler


def get_asr_handler(request: Request) -> ASRHandler:
    return request.app.state.asr_handler
