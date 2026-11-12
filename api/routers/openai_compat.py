"""OpenAI-compatible /v1/models and /v1/chat/completions for Cursor and other clients."""
from __future__ import annotations

import json
from typing import Any, Dict, Generator, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from models.llm_handler import LLMHandler
from utils.metrics import OPENAI_CHAT_TOTAL

router = APIRouter(tags=["openai-compat"])


class FunctionDef(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class ToolDef(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "function"
    function: FunctionDef


class FunctionCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    arguments: Optional[str] = None


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[str] = None
    type: str = "function"
    function: Optional[FunctionCall] = None
    index: Optional[int] = None


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: Optional[str] = None
    messages: List[ChatMessage] = Field(..., min_length=1)
    temperature: Optional[float] = None
    stream: bool = False
    tools: Optional[List[ToolDef]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    # Accepted but ignored / partially used — keep clients happy.
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    stop: Optional[Union[str, List[str]]] = None
    user: Optional[str] = None
    parallel_tool_calls: Optional[bool] = None


def get_llm_handler(request: Request) -> LLMHandler:
    return request.app.state.llm_handler


def _message_to_dict(m: ChatMessage) -> Dict[str, Any]:
    """Serialize a chat message for the OpenAI / Ollama client (tool-loop aware)."""
    out: Dict[str, Any] = {"role": m.role}
    if m.content is not None:
        out["content"] = m.content
    elif m.tool_calls:
        out["content"] = None
    else:
        out["content"] = ""
    if m.name:
        out["name"] = m.name
    if m.tool_call_id:
        out["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        out["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in m.tool_calls]
    return out


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
    messages = [_message_to_dict(m) for m in body.messages]
    tools = [t.model_dump(exclude_none=True) for t in body.tools] if body.tools else None
    try:
        result = llm_handler.chat_completion(
            messages,
            model=body.model,
            temperature=body.temperature,
            stream=body.stream,
            tools=tools,
            tool_choice=body.tool_choice,
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
