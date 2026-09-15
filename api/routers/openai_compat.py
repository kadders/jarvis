"""OpenAI-compatible /v1/models and /v1/chat/completions for Cursor and other clients."""
from __future__ import annotations

import json
from typing import Any, Dict, Generator, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from models.llm_handler import LLMHandler
from utils.metrics import OPENAI_CHAT_TOTAL

router = APIRouter(tags=["openai-compat"])


class ChatMessage(BaseModel):
    role: str
    content: Optional[str] = ""


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage] = Field(..., min_length=1)
    temperature: Optional[float] = None
    stream: bool = False
    # Accepted but ignored / partially used — keep clients happy.
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    stop: Optional[Union[str, List[str]]] = None
    user: Optional[str] = None


def get_llm_handler(request: Request) -> LLMHandler:
    return request.app.state.llm_handler


@router.get("/models")
async def list_models(llm_handler: LLMHandler = Depends(get_llm_handler)):
    return {
        "object": "list",
        "data": llm_handler.list_model_entries(),
    }


@router.get("/models/{model_id}")
async def get_model(model_id: str, llm_handler: LLMHandler = Depends(get_llm_handler)):
    for entry in llm_handler.list_model_entries():
        if entry["id"] == model_id:
            return entry
    raise HTTPException(status_code=404, detail="Model not found")


def _sse_lines(chunks: Generator[Dict[str, Any], None, None]) -> Generator[str, None, None]:
    try:
        for chunk in chunks:
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        logger.error(f"SSE stream failed: {e}")
        err = {
            "error": {
                "message": str(e),
                "type": "server_error",
            }
        }
        yield f"data: {json.dumps(err)}\n\n"
        yield "data: [DONE]\n\n"


@router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    llm_handler: LLMHandler = Depends(get_llm_handler),
):
    messages = [{"role": m.role, "content": m.content or ""} for m in body.messages]
    try:
        result = llm_handler.chat_completion(
            messages,
            model=body.model,
            temperature=body.temperature,
            stream=body.stream,
        )
    except Exception as e:
        OPENAI_CHAT_TOTAL.labels("error").inc()
        logger.error(f"chat.completions failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    if body.stream:
        OPENAI_CHAT_TOTAL.labels("stream").inc()
        return StreamingResponse(
            _sse_lines(result),  # type: ignore[arg-type]
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    OPENAI_CHAT_TOTAL.labels("sync").inc()
    return result
