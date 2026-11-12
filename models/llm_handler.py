import os
import uuid
import json
from typing import Dict, Any, Tuple, Optional, List, Generator
from loguru import logger
import time

from utils.request_context import outbound_trace_headers

try:
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    logger.warning("Transformers package not available, using mock mode")
    TRANSFORMERS_AVAILABLE = False


def sanitize_model_alias(name: str) -> str:
    """Cursor-friendly alias: llama3.1:8b → llama31-8b (drop dots; colon → dash)."""
    out = []
    for ch in name:
        if ch.isalnum():
            out.append(ch.lower())
        elif ch == ":":
            out.append("-")
        elif ch in ("-", "_"):
            out.append("-")
        # drop '.' so 3.1 → 31
    alias = "".join(out).strip("-")
    while "--" in alias:
        alias = alias.replace("--", "-")
    return alias or "model"


class LLMHandler:
    """
    Handles LLM-based processing of incoming events and OpenAI-compatible chat.

    Supports a hybrid two-tier Ollama deployment:
        - default ("big") tier: high-quality, slower (e.g. gpt-oss:20b on N GPUs)
        - "fast" tier: optional, smaller model on its own pod(s) (e.g. llama3.1:8b)

    Routing: an event with metadata.model_tier == "fast" hits the fast tier
    when configured; otherwise the default tier is used.
    """

    def __init__(
        self,
        model_path: str,
        model_type: str = "huggingface",
        model_name: str = "google/flan-t5-base",
        ollama_base_url: str = "http://localhost:11434/v1",
        temperature: float = 0.7,
        *,
        model_name_fast: Optional[str] = None,
        ollama_fast_base_url: Optional[str] = None,
        model_name_tuned: Optional[str] = None,
        feedback_store: Any = None,
        fallback_enabled: bool = False,
        fallback_base_url: Optional[str] = None,
        fallback_api_key: Optional[str] = None,
        fallback_model: Optional[str] = None,
        fallback_timeout_s: float = 60.0,
    ):
        self.model_path = model_path
        self.model_type = model_type.lower().strip()
        self.model_name = model_name
        self.temperature = temperature
        self.ollama_base_url = self._normalize_v1(ollama_base_url)
        self.model_name_fast = model_name_fast
        self.ollama_fast_base_url = (
            self._normalize_v1(ollama_fast_base_url) if ollama_fast_base_url else None
        )
        self.model_name_tuned = (model_name_tuned or "").strip() or None
        self.feedback_store = feedback_store
        self.fallback_enabled = bool(fallback_enabled)
        self.fallback_model = (fallback_model or "gpt-4o-mini").strip()
        self.fallback_timeout_s = float(fallback_timeout_s)
        self.events_store: Dict[str, Any] = {}
        self.completions_store: Dict[str, Any] = {}
        self.model = None
        self.tokenizer = None
        self._ollama_client = None
        self._ollama_fast_client = None
        self._fallback_client = None

        if self.fallback_enabled and fallback_base_url and fallback_api_key:
            from openai import OpenAI

            self._fallback_client = OpenAI(
                base_url=self._normalize_v1(fallback_base_url),
                api_key=fallback_api_key,
                timeout=self.fallback_timeout_s,
            )
            logger.info(
                f"LLMHandler cloud fallback enabled "
                f"base={self._normalize_v1(fallback_base_url)} "
                f"model={self.fallback_model}"
            )
        elif self.fallback_enabled:
            logger.warning(
                "JARVIS_FALLBACK_ENABLED but base URL or API key missing; fallback inactive"
            )
        if self.model_type == "mock":
            logger.info("LLMHandler explicit mock mode")
        elif self.model_type == "ollama":
            from openai import OpenAI
            self._ollama_client = OpenAI(
                base_url=self.ollama_base_url,
                api_key="ollama",
            )
            logger.info(
                f"LLMHandler default Ollama at {self.ollama_base_url} model={model_name}"
            )
            if self.model_name_fast and self.ollama_fast_base_url:
                self._ollama_fast_client = OpenAI(
                    base_url=self.ollama_fast_base_url,
                    api_key="ollama",
                )
                logger.info(
                    f"LLMHandler fast Ollama at {self.ollama_fast_base_url} "
                    f"model={self.model_name_fast}"
                )
        elif self.model_type == "huggingface" and TRANSFORMERS_AVAILABLE:
            logger.info(f"Loading HF model {model_name} from {model_path}")
            try:
                self._load_hf_model()
            except Exception as e:
                logger.error(f"Error loading model: {str(e)}")
                logger.warning("Using mock model for development")
                self.model = None
                self.tokenizer = None
        else:
            if self.model_type == "huggingface" and not TRANSFORMERS_AVAILABLE:
                logger.warning("Transformers unavailable; using mock model")
            else:
                logger.warning(f"Unknown model_type={model_type}; using mock")
            self.model = None
            self.tokenizer = None

    @staticmethod
    def _normalize_v1(url: str) -> str:
        u = url.rstrip("/")
        return u if u.endswith("/v1") else f"{u}/v1"

    def _load_hf_model(self) -> None:
        if not TRANSFORMERS_AVAILABLE:
            raise RuntimeError("transformers not installed")
        if os.path.exists(self.model_path) and os.listdir(self.model_path):
            logger.info(f"Loading model from local path: {self.model_path}")
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(self.model_path)
        else:
            logger.info(f"Downloading model from Hugging Face: {self.model_name}")
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)
            os.makedirs(self.model_path, exist_ok=True)
            self.tokenizer.save_pretrained(self.model_path)
            self.model.save_pretrained(self.model_path)

    def _format_prompt(self, event_type: str, content: Dict[str, Any]) -> str:
        """Format a prompt for the LLM based on the event type and content"""
        if event_type == "text_query":
            return f"Query: {content.get('text', '')}\nResponse:"
        elif event_type == "code_generation":
            lang = str(content.get("language", "python")).lower()
            preamble = ""
            if lang in ("python", "py"):
                preamble = "Follow Black formatting (88-char lines). "
            elif lang in ("arduino", "cpp", "c++"):
                preamble = (
                    "Use non-blocking ESP32/Arduino patterns (millis, not delay in loop), "
                    "sized JsonDocument, and http.end(). "
                )
            return (
                f"Generate code for: {content.get('description', '')}\n"
                f"{preamble}"
                f"Language: {content.get('language', 'python')}\nCode:"
            )
        elif event_type == "data_analysis":
            return f"Analyze this data: {json.dumps(content.get('data', {}))}\nAnalysis:"
        else:
            return f"Process this event: {json.dumps(content)}\nResponse:"

    def _ollama_messages(
        self, event_type: str, prompt: str
    ) -> Tuple[List[Dict[str, str]], bool]:
        system = f"You are a helpful assistant. Event type: {event_type}."
        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        note = self._correction_note(prompt)
        learning = bool(note)
        if note:
            messages.append({"role": "system", "content": note})
        messages.append({"role": "user", "content": prompt})
        return messages, learning

    def _prefer_local(self, event_data: Optional[Dict[str, Any]] = None) -> bool:
        """MCP / RL traffic stays on local Ollama (no cloud fallback)."""
        metadata = (event_data or {}).get("metadata") or {}
        if metadata.get("prefer_local") is True:
            return True
        source = str(metadata.get("source") or "").lower()
        return source in ("mcp", "jarvis_mcp", "reinforcement")

    def _correction_note(self, prompt_or_user_text: str) -> Optional[str]:
        store = self.feedback_store
        if store is None:
            return None
        try:
            return store.few_shot_system_note(prompt_or_user_text)
        except Exception as e:
            logger.warning(f"feedback few-shot lookup failed: {e}")
            return None

    def _build_envelope(
        self,
        event_data: Dict[str, Any],
        formatted_prompt: str,
        result_text: str,
        backend: str,
        model_used: Optional[str],
        *,
        usage: Optional[Dict[str, Any]] = None,
        finish_reason: Optional[str] = None,
        error: bool = False,
        error_detail: Optional[str] = None,
        timing_ms: Optional[int] = None,
        tier: Optional[str] = None,
        learning_applied: bool = False,
    ) -> Dict[str, Any]:
        envelope = {
            "request": {
                "event_type": event_data.get("event_type"),
                "content": event_data.get("content"),
                "formatted_prompt": formatted_prompt,
            },
            "result": {
                "text": result_text,
                "model": model_used or self.model_name,
                "finish_reason": finish_reason,
                "usage": usage,
            },
            "meta": {
                "timing_ms": timing_ms if timing_ms is not None else 0,
                "backend": backend,
                "error": error,
                "learning_applied": learning_applied,
            },
        }
        if tier:
            envelope["meta"]["tier"] = tier
        if error_detail:
            envelope["meta"]["error_detail"] = error_detail
        return envelope

    def _resolve_tier(self, event_data: Dict[str, Any]) -> str:
        metadata = event_data.get("metadata") or {}
        requested = (metadata.get("model_tier") or "default").lower().strip()
        if requested == "fast" and self._ollama_fast_client:
            return "fast"
        return "default"

    def list_model_entries(self) -> List[Dict[str, Any]]:
        """OpenAI-style model catalog (canonical ids + Cursor-friendly aliases)."""
        created = int(time.time())
        entries: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def add(model_id: str, owned_by: str = "jarvis") -> None:
            if not model_id or model_id in seen:
                return
            seen.add(model_id)
            entries.append(
                {
                    "id": model_id,
                    "object": "model",
                    "created": created,
                    "owned_by": owned_by,
                }
            )
            alias = sanitize_model_alias(model_id)
            if alias and alias not in seen and alias != model_id:
                seen.add(alias)
                entries.append(
                    {
                        "id": alias,
                        "object": "model",
                        "created": created,
                        "owned_by": owned_by,
                    }
                )

        if self.model_type == "mock":
            add("mock_model")
            add(self.model_name)
            if self.model_name_fast:
                add(self.model_name_fast)
        else:
            add(self.model_name)
            if self.model_name_fast:
                add(self.model_name_fast)
            if self.model_name_tuned:
                add(self.model_name_tuned)
            if self._fallback_client and self.fallback_model:
                add(self.fallback_model, owned_by="jarvis-fallback")
        return entries

    def _can_fallback(self) -> bool:
        return bool(self.fallback_enabled and self._fallback_client and self.fallback_model)

    @staticmethod
    def _serialize_tool_calls(raw: Any) -> Optional[List[Dict[str, Any]]]:
        """Normalize OpenAI SDK / dict tool_calls into plain JSON-ready dicts."""
        if not raw:
            return None
        out: List[Dict[str, Any]] = []
        for i, tc in enumerate(raw):
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                if not isinstance(fn, dict):
                    fn = {
                        "name": getattr(fn, "name", None),
                        "arguments": getattr(fn, "arguments", None),
                    }
                args = fn.get("arguments")
                if args is not None and not isinstance(args, str):
                    args = json.dumps(args)
                entry: Dict[str, Any] = {
                    "id": tc.get("id") or f"call_{i}",
                    "type": tc.get("type") or "function",
                    "function": {
                        "name": fn.get("name") or "",
                        "arguments": args if args is not None else "{}",
                    },
                }
                if "index" in tc and tc["index"] is not None:
                    entry["index"] = tc["index"]
                out.append(entry)
                continue
            fn = getattr(tc, "function", None)
            args = getattr(fn, "arguments", None) if fn is not None else None
            if args is not None and not isinstance(args, str):
                args = json.dumps(args)
            entry = {
                "id": getattr(tc, "id", None) or f"call_{i}",
                "type": getattr(tc, "type", None) or "function",
                "function": {
                    "name": getattr(fn, "name", None) or "",
                    "arguments": args if args is not None else "{}",
                },
            }
            idx = getattr(tc, "index", None)
            if idx is not None:
                entry["index"] = idx
            out.append(entry)
        return out or None

    @staticmethod
    def _normalize_chat_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Preserve tool-loop fields; do not flatten to role/content-only."""
        out: List[Dict[str, Any]] = []
        for m in messages:
            msg: Dict[str, Any] = {"role": m.get("role") or "user"}
            if "content" in m:
                msg["content"] = m.get("content")
            elif m.get("tool_calls"):
                msg["content"] = None
            else:
                msg["content"] = ""
            if m.get("name"):
                msg["name"] = m["name"]
            if m.get("tool_call_id"):
                msg["tool_call_id"] = m["tool_call_id"]
            if m.get("tool_calls"):
                msg["tool_calls"] = m["tool_calls"]
            out.append(msg)
        return out

    def _run_fallback_once(
        self,
        *,
        messages: List[Dict[str, Any]],
        temperature: float,
        reason: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> Tuple[
        Optional[str],
        Optional[Dict[str, Any]],
        Optional[str],
        str,
        str,
        Optional[List[Dict[str, Any]]],
    ]:
        from utils.metrics import FALLBACK_TOTAL

        assert self._fallback_client is not None
        model = self.fallback_model
        try:
            create_kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if tools:
                create_kwargs["tools"] = tools
            if tool_choice is not None:
                create_kwargs["tool_choice"] = tool_choice
            trace = outbound_trace_headers()
            if trace:
                create_kwargs["extra_headers"] = trace
            completion = self._fallback_client.chat.completions.create(**create_kwargs)
            choice = completion.choices[0]
            msg = choice.message
            tool_calls = self._serialize_tool_calls(getattr(msg, "tool_calls", None))
            text = (msg.content or "").strip() if msg.content else (None if tool_calls else "")
            if tool_calls and text == "":
                text = None
            usage = completion.usage.model_dump() if completion.usage else None
            FALLBACK_TOTAL.labels("ok").inc()
            logger.warning(f"cloud fallback used reason={reason!r} model={model}")
            return (
                text,
                usage,
                choice.finish_reason,
                "fallback",
                completion.model or model,
                tool_calls,
            )
        except Exception as e:
            FALLBACK_TOTAL.labels("error").inc()
            logger.error(f"cloud fallback failed: {e}")
            return f"Error: {e}", None, "error", "fallback", model, None

    def resolve_chat_model(self, model: Optional[str]) -> Tuple[str, str, Any]:
        """
        Map requested model id/alias → (tier, ollama_model_name, client_or_None).

        tier is one of: default | fast | tuned
        """
        requested = (model or self.model_name or "").strip()
        alias_req = sanitize_model_alias(requested) if requested else ""

        def matches(candidate: Optional[str]) -> bool:
            if not candidate:
                return False
            if requested == candidate:
                return True
            return bool(alias_req) and sanitize_model_alias(candidate) == alias_req

        if matches(self.model_name_tuned):
            # Tuned models are served from the default (big) Ollama tier.
            return "tuned", self.model_name_tuned or self.model_name, self._ollama_client

        if matches(self.fallback_model) and self._can_fallback():
            return "fallback", self.fallback_model, self._fallback_client

        if matches(self.model_name_fast) and (
            self._ollama_fast_client or self.model_type == "mock"
        ):
            return "fast", self.model_name_fast or self.model_name, self._ollama_fast_client

        if matches(self.model_name) or not requested:
            return "default", self.model_name, self._ollama_client

        # Unknown id: treat as default-tier passthrough (Ollama may still accept it).
        return "default", requested, self._ollama_client

    def process_event(self, event_data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        event_id = str(uuid.uuid4())
        event_type = event_data.get("event_type")
        content = event_data.get("content", {})
        formatted_prompt = self._format_prompt(event_type, content)
        t0 = time.perf_counter()

        if self.model_type == "ollama" and self._ollama_client:
            tier = self._resolve_tier(event_data)
            envelope = self._run_ollama(event_data, formatted_prompt, t0, tier=tier)
        elif (
            self.model
            and self.tokenizer
            and TRANSFORMERS_AVAILABLE
            and self.model_type == "huggingface"
        ):
            envelope = self._run_huggingface(event_data, formatted_prompt, t0)
        else:
            envelope = self._run_mock(event_data, formatted_prompt, t0)

        self.events_store[event_id] = {
            "event_data": event_data,
            "response": envelope,
            "status": "completed",
            "timestamp": time.time(),
        }
        return event_id, envelope

    def _inject_corrections_into_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Inject few-shot corrections as a system note without rewriting tool-loop turns."""
        user_bits = [
            m.get("content", "")
            for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), str) and m.get("content")
        ]
        prompt = "\n".join(user_bits)
        note = self._correction_note(prompt)
        if not note:
            return messages, False
        out = list(messages)
        insert_at = 0
        if out and out[0].get("role") == "system":
            insert_at = 1
        out.insert(insert_at, {"role": "system", "content": note})
        return out, True

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> Dict[str, Any] | Generator[Dict[str, Any], None, None]:
        """OpenAI-compatible chat completion (sync or streaming generator of chunk dicts)."""
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        tier, resolved_model, client = self.resolve_chat_model(model)
        temp = self.temperature if temperature is None else temperature
        msgs, learning = self._inject_corrections_into_messages(
            self._normalize_chat_messages(messages)
        )
        t0 = time.perf_counter()

        if stream:
            return self._chat_stream(
                completion_id=completion_id,
                created=created,
                messages=msgs,
                model=resolved_model,
                temperature=temp,
                tier=tier,
                client=client,
                t0=t0,
                learning_applied=learning,
                tools=tools,
                tool_choice=tool_choice,
            )

        text, usage, finish_reason, backend, model_used, tool_calls = self._chat_once(
            messages=msgs,
            model=resolved_model,
            temperature=temp,
            tier=tier,
            client=client,
            tools=tools,
            tool_choice=tool_choice,
        )
        timing_ms = int((time.perf_counter() - t0) * 1000)
        assistant_message: Dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
            if text is None:
                assistant_message["content"] = None
        fr = finish_reason or ("tool_calls" if tool_calls else "stop")
        payload = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model_used or resolved_model,
            "choices": [
                {
                    "index": 0,
                    "message": assistant_message,
                    "finish_reason": fr,
                }
            ],
            "usage": usage
            or {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "jarvis": {
                "tier": tier if backend != "fallback" else "fallback",
                "backend": backend,
                "timing_ms": timing_ms,
                "fallback": backend == "fallback",
                "learning_applied": learning,
            },
        }
        store_text = text or ""
        if tool_calls:
            store_text = json.dumps({"content": text, "tool_calls": tool_calls})
        self._store_completion(
            completion_id,
            messages=msgs,
            response_text=store_text,
            model=model_used or resolved_model,
            tier=tier,
            stream=False,
        )
        return payload

    def _store_completion(
        self,
        completion_id: str,
        *,
        messages: List[Dict[str, Any]],
        response_text: str,
        model: str,
        tier: str,
        stream: bool,
    ) -> None:
        prompt_parts: List[str] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if content:
                prompt_parts.append(f"{role}: {content}")
            elif m.get("tool_calls"):
                prompt_parts.append(f"{role}: [tool_calls]")
            elif m.get("tool_call_id"):
                prompt_parts.append(f"{role}: [tool_result {m.get('tool_call_id')}]")
        prompt = "\n".join(prompt_parts)
        self.completions_store[completion_id] = {
            "completion_id": completion_id,
            "messages": messages,
            "prompt": prompt,
            "response": response_text,
            "model": model,
            "tier": tier,
            "stream": stream,
            "timestamp": time.time(),
        }

    def lookup_completion(self, completion_id: str) -> Optional[Dict[str, Any]]:
        return self.completions_store.get(completion_id)

    def _chat_once(
        self,
        *,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        tier: str,
        client: Any,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> Tuple[
        Optional[str],
        Optional[Dict[str, Any]],
        Optional[str],
        str,
        str,
        Optional[List[Dict[str, Any]]],
    ]:
        if tier == "fallback" and self._can_fallback():
            return self._run_fallback_once(
                messages=messages,
                temperature=temperature,
                reason="explicit_fallback_model",
                tools=tools,
                tool_choice=tool_choice,
            )

        if self.model_type == "ollama" and client is not None:
            try:
                create_kwargs: Dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if tools:
                    create_kwargs["tools"] = tools
                if tool_choice is not None:
                    create_kwargs["tool_choice"] = tool_choice
                trace = outbound_trace_headers()
                if trace:
                    create_kwargs["extra_headers"] = trace
                completion = client.chat.completions.create(**create_kwargs)
                choice = completion.choices[0]
                msg = choice.message
                tool_calls = self._serialize_tool_calls(getattr(msg, "tool_calls", None))
                text = (msg.content or "").strip() if msg.content else (None if tool_calls else "")
                if tool_calls and text == "":
                    text = None
                usage = completion.usage.model_dump() if completion.usage else None
                return (
                    text,
                    usage,
                    choice.finish_reason,
                    "ollama",
                    completion.model or model,
                    tool_calls,
                )
            except Exception as e:
                logger.error(f"Ollama chat error (tier={tier}): {e}")
                if self._can_fallback():
                    return self._run_fallback_once(
                        messages=messages,
                        temperature=temperature,
                        reason=f"ollama_error:{e}",
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                return f"Error: {e}", None, "error", "ollama", model, None

        if (
            self.model
            and self.tokenizer
            and TRANSFORMERS_AVAILABLE
            and self.model_type == "huggingface"
        ):
            prompt = "\n".join(
                (m.get("content") or "") for m in messages if isinstance(m.get("content"), str)
            )
            try:
                inputs = self.tokenizer(prompt, return_tensors="pt")
                outputs = self.model.generate(
                    inputs.input_ids,
                    max_new_tokens=100,
                    temperature=temperature,
                    do_sample=True,
                )
                response_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
                if response_text.startswith(prompt):
                    response_text = response_text[len(prompt) :].strip()
                return response_text, None, "stop", "huggingface", self.model_name, None
            except Exception as e:
                if self._can_fallback():
                    return self._run_fallback_once(
                        messages=messages,
                        temperature=temperature,
                        reason=f"hf_error:{e}",
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                return (
                    f"Error generating response: {e}",
                    None,
                    "error",
                    "huggingface",
                    model,
                    None,
                )

        # Mock — emit a deterministic tool call when tools are provided.
        time.sleep(0.05)
        if tools:
            chosen_name = None
            if isinstance(tool_choice, dict):
                chosen_name = (
                    (tool_choice.get("function") or {}).get("name")
                    if tool_choice.get("type") == "function"
                    else None
                )
            elif isinstance(tool_choice, str) and tool_choice not in (
                "auto",
                "none",
                "required",
            ):
                chosen_name = tool_choice
            if not chosen_name:
                first = tools[0] if tools else {}
                chosen_name = (first.get("function") or {}).get("name") or "mock_tool"
            tool_calls = [
                {
                    "id": f"call_mock_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {"name": chosen_name, "arguments": "{}"},
                }
            ]
            return (
                None,
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                "tool_calls",
                "mock",
                model or "mock_model",
                tool_calls,
            )
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content") or ""
                break
        text = f"Mock response for query: {last_user}" if last_user else "Mock chat response"
        return (
            text,
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "stop",
            "mock",
            model or "mock_model",
            None,
        )

    def _chat_stream(
        self,
        *,
        completion_id: str,
        created: int,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        tier: str,
        client: Any,
        t0: float,
        learning_applied: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        full_text_parts: List[str] = []
        collected_tool_calls: List[Dict[str, Any]] = []

        def role_chunk() -> Dict[str, Any]:
            return {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            }

        def content_chunk(piece: str) -> Dict[str, Any]:
            return {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": piece},
                        "finish_reason": None,
                    }
                ],
            }

        def tool_calls_chunk(tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
            return {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": tool_calls},
                        "finish_reason": None,
                    }
                ],
            }

        def done_chunk(finish_reason: str = "stop") -> Dict[str, Any]:
            return {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": finish_reason,
                    }
                ],
            }

        def emit_delta(delta: Any) -> Generator[Dict[str, Any], None, None]:
            piece = getattr(delta, "content", None) or ""
            if piece:
                full_text_parts.append(piece)
                yield content_chunk(piece)
            raw_tcs = getattr(delta, "tool_calls", None)
            serialized = self._serialize_tool_calls(raw_tcs)
            if serialized:
                # Ensure stream deltas carry index for OpenAI clients.
                for i, tc in enumerate(serialized):
                    if "index" not in tc:
                        tc["index"] = i
                collected_tool_calls.extend(serialized)
                yield tool_calls_chunk(serialized)

        if tier == "fallback" and self._can_fallback():
            try:
                create_kwargs: Dict[str, Any] = {
                    "model": self.fallback_model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                }
                if tools:
                    create_kwargs["tools"] = tools
                if tool_choice is not None:
                    create_kwargs["tool_choice"] = tool_choice
                stream = self._fallback_client.chat.completions.create(**create_kwargs)
                model = self.fallback_model
                yield role_chunk()
                for event in stream:
                    if not event.choices:
                        continue
                    yield from emit_delta(event.choices[0].delta)
                    fr = event.choices[0].finish_reason
                    if fr:
                        yield done_chunk(fr)
                        break
                else:
                    yield done_chunk("tool_calls" if collected_tool_calls else "stop")
            except Exception as e:
                text, _, fr, _, model_used, tcs = self._run_fallback_once(
                    messages=messages,
                    temperature=temperature,
                    reason=f"fallback_stream_error:{e}",
                    tools=tools,
                    tool_choice=tool_choice,
                )
                model = model_used
                yield role_chunk()
                if tcs:
                    collected_tool_calls.extend(tcs)
                    indexed = []
                    for i, tc in enumerate(tcs):
                        item = dict(tc)
                        item.setdefault("index", i)
                        indexed.append(item)
                    yield tool_calls_chunk(indexed)
                elif text:
                    full_text_parts.append(text)
                    yield content_chunk(text)
                else:
                    yield content_chunk("")
                yield done_chunk(fr or ("tool_calls" if tcs else "stop"))
        elif self.model_type == "ollama" and client is not None:
            try:
                create_kwargs = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                }
                if tools:
                    create_kwargs["tools"] = tools
                if tool_choice is not None:
                    create_kwargs["tool_choice"] = tool_choice
                trace = outbound_trace_headers()
                if trace:
                    create_kwargs["extra_headers"] = trace
                stream = client.chat.completions.create(**create_kwargs)
                yield role_chunk()
                for event in stream:
                    if not event.choices:
                        continue
                    yield from emit_delta(event.choices[0].delta)
                    fr = event.choices[0].finish_reason
                    if fr:
                        yield done_chunk(fr)
                        break
                else:
                    yield done_chunk("tool_calls" if collected_tool_calls else "stop")
            except Exception as e:
                logger.error(f"Ollama stream error (tier={tier}): {e}")
                if self._can_fallback():
                    text, _, fr, _, model_used, tcs = self._run_fallback_once(
                        messages=messages,
                        temperature=temperature,
                        reason=f"ollama_stream_error:{e}",
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                    model = model_used
                    yield role_chunk()
                    if tcs:
                        collected_tool_calls.extend(tcs)
                        indexed = []
                        for i, tc in enumerate(tcs):
                            item = dict(tc)
                            item.setdefault("index", i)
                            indexed.append(item)
                        yield tool_calls_chunk(indexed)
                        yield done_chunk(fr or "tool_calls")
                    else:
                        step = max(1, len(text) // 4) if text else 1
                        if not text:
                            yield content_chunk("")
                        else:
                            for i in range(0, len(text), step):
                                piece = text[i : i + step]
                                full_text_parts.append(piece)
                                yield content_chunk(piece)
                        yield done_chunk(fr or "stop")
                else:
                    yield role_chunk()
                    err = f"Error: {e}"
                    full_text_parts.append(err)
                    yield content_chunk(err)
                    yield done_chunk("error")
        else:
            # Mock / HF: emit role + content and/or tool_calls.
            text, _, finish_reason, _, model_used, tcs = self._chat_once(
                messages=messages,
                model=model,
                temperature=temperature,
                tier=tier,
                client=client,
                tools=tools,
                tool_choice=tool_choice,
            )
            model = model_used or model
            yield role_chunk()
            if tcs:
                collected_tool_calls.extend(tcs)
                indexed = []
                for i, tc in enumerate(tcs):
                    item = dict(tc)
                    item.setdefault("index", i)
                    indexed.append(item)
                yield tool_calls_chunk(indexed)
                yield done_chunk(finish_reason or "tool_calls")
            else:
                step = max(1, len(text) // 4) if text else 1
                if not text:
                    yield content_chunk("")
                else:
                    for i in range(0, len(text), step):
                        piece = text[i : i + step]
                        full_text_parts.append(piece)
                        yield content_chunk(piece)
                yield done_chunk(finish_reason or "stop")

        store_text = "".join(full_text_parts)
        if collected_tool_calls:
            store_text = json.dumps(
                {"content": store_text or None, "tool_calls": collected_tool_calls}
            )
        self._store_completion(
            completion_id,
            messages=messages,
            response_text=store_text,
            model=model,
            tier=tier,
            stream=True,
        )
        _ = (t0, learning_applied)  # reserved for future jarvis stream trailer metrics

    def _run_ollama(
        self,
        event_data: Dict[str, Any],
        formatted_prompt: str,
        t0: float,
        *,
        tier: str = "default",
    ) -> Dict[str, Any]:
        if tier == "fast":
            client = self._ollama_fast_client
            model = self.model_name_fast or self.model_name
        else:
            client = self._ollama_client
            model = self.model_name

        messages, learning = self._ollama_messages(
            event_data.get("event_type", ""), formatted_prompt
        )
        prefer_local = self._prefer_local(event_data)
        try:
            create_kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": self.temperature,
            }
            trace = outbound_trace_headers()
            if trace:
                create_kwargs["extra_headers"] = trace
            completion = client.chat.completions.create(**create_kwargs)
            choice = completion.choices[0]
            msg = choice.message
            text = (msg.content or "").strip()
            usage = completion.usage.model_dump() if completion.usage else None
            timing_ms = int((time.perf_counter() - t0) * 1000)
            return self._build_envelope(
                event_data,
                formatted_prompt,
                text,
                "ollama",
                completion.model,
                usage=usage,
                finish_reason=choice.finish_reason,
                timing_ms=timing_ms,
                tier=tier,
                learning_applied=learning,
            )
        except Exception as e:
            logger.error(f"Ollama error (tier={tier}): {e}")
            if self._can_fallback() and not prefer_local:
                text, usage, finish_reason, backend, model_used, _tcs = self._run_fallback_once(
                    messages=messages,
                    temperature=self.temperature,
                    reason=f"ollama_event_error:{e}",
                )
                timing_ms = int((time.perf_counter() - t0) * 1000)
                return self._build_envelope(
                    event_data,
                    formatted_prompt,
                    text,
                    backend,
                    model_used,
                    usage=usage,
                    finish_reason=finish_reason,
                    timing_ms=timing_ms,
                    tier="fallback",
                    learning_applied=learning,
                )
            timing_ms = int((time.perf_counter() - t0) * 1000)
            return self._build_envelope(
                event_data,
                formatted_prompt,
                f"Error: {e}",
                "ollama",
                model,
                error=True,
                error_detail=str(e),
                timing_ms=timing_ms,
                tier=tier,
                learning_applied=learning,
            )

    def _run_huggingface(
        self, event_data: Dict[str, Any], formatted_prompt: str, t0: float
    ) -> Dict[str, Any]:
        try:
            inputs = self.tokenizer(formatted_prompt, return_tensors="pt")
            outputs = self.model.generate(
                inputs.input_ids,
                max_new_tokens=100,
                temperature=self.temperature,
                do_sample=True,
            )
            response_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            if response_text.startswith(formatted_prompt):
                response_text = response_text[len(formatted_prompt) :].strip()
            timing_ms = int((time.perf_counter() - t0) * 1000)
            return self._build_envelope(
                event_data,
                formatted_prompt,
                response_text,
                "huggingface",
                self.model_name,
                timing_ms=timing_ms,
            )
        except Exception as e:
            logger.error(f"Error generating LLM response: {str(e)}")
            timing_ms = int((time.perf_counter() - t0) * 1000)
            return self._build_envelope(
                event_data,
                formatted_prompt,
                f"Error generating response: {str(e)}",
                "huggingface",
                self.model_name,
                error=True,
                error_detail=str(e),
                timing_ms=timing_ms,
            )

    def _run_mock(
        self, event_data: Dict[str, Any], formatted_prompt: str, t0: float
    ) -> Dict[str, Any]:
        time.sleep(0.05)
        event_type = event_data.get("event_type")
        content = event_data.get("content", {})
        if event_type == "text_query":
            text = f"Mock response for query: {content.get('text', '')}"
        elif event_type == "code_generation":
            text = (
                f"# Mock code for: {content.get('description', '')}\n\n"
                "def example_function():\n    print('Hello world')\n\nexample_function()"
            )
        else:
            text = f"Mock response for event type: {event_type}"
        timing_ms = int((time.perf_counter() - t0) * 1000)
        # Expose any requested tier in mock responses to make routing testable.
        requested_tier = (
            (event_data.get("metadata") or {}).get("model_tier") or "default"
        )
        return self._build_envelope(
            event_data,
            formatted_prompt,
            text,
            "mock",
            "mock_model",
            timing_ms=timing_ms,
            tier=requested_tier,
        )

    def get_event_status(self, event_id: str) -> Optional[Dict[str, Any]]:
        return self.events_store.get(event_id)

    def async_process(self, event_data: Dict[str, Any]) -> None:
        """Background processing hook (kept for API compatibility)."""
        event_id = str(uuid.uuid4())
        self.events_store[event_id] = {
            "event_data": event_data,
            "status": "processing",
            "timestamp": time.time(),
        }
        try:
            time.sleep(0.1)
            new_id, response = self.process_event(event_data)
            self.events_store[event_id].update(
                {
                    "status": "completed",
                    "response": response,
                    "event_id": new_id,
                }
            )
        except Exception as e:
            logger.error(f"Error in background processing: {str(e)}")
            self.events_store[event_id]["status"] = "error"
            self.events_store[event_id]["error"] = str(e)
