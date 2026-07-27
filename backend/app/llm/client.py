from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncGenerator, Literal

import httpx
from loguru import logger

from app.config import settings
from .types import ChatResult, Message, StreamChunk, ToolCall, ToolDef, Usage
from .registry import get as _get_model, get_ordered as _get_ordered
from .tracker import TokenTracker

RETRYABLE_STATUSES = {429, 500, 502, 503}
MAX_RETRIES = 3


def _sanitize_error(msg: str) -> str:
    msg = re.sub(r'Bearer\s+\S+', 'Bearer [REDACTED]', msg)
    msg = re.sub(r'(?i)(authorization|api[_-]?key)\s*[:=]\s*\S+', r'\1: [REDACTED]', msg)
    return msg


class LLMError(Exception):
    pass


class LLMNonRetryableError(LLMError):
    """Raised for non-retryable errors (4xx). Current model is skipped
    and the next fallback model is tried."""


class LLMRetryExhaustedError(LLMError):
    """Raised after exhausting retries on the current model. The next
    fallback model is tried."""


_tracker = TokenTracker()
_shared_client: "LLMClient | None" = None


def get_tracker() -> TokenTracker:
    return _tracker


def get_shared_client() -> "LLMClient":
    global _shared_client
    if _shared_client is None:
        _shared_client = LLMClient()
    return _shared_client


async def close_shared_client():
    global _shared_client
    if _shared_client is not None:
        await _shared_client.close()
        _shared_client = None


def _message_to_dict(msg: Message) -> dict:
    return msg.to_dict()


def _tool_def_to_dict(td: ToolDef) -> dict:
    return {"type": td.type, "function": td.function}


def _resolve_fallback_models() -> list[str]:
    """Parse the comma-separated fallback models from settings."""
    raw = getattr(settings, "llm_fallback_models", None)
    if not raw:
        return []
    return [m.strip() for m in raw.split(",") if m.strip()]


def _resolve_models(requested: str) -> list[str]:
    ordered = _get_ordered()
    if not ordered:
        return [requested]
    if requested in ordered:
        idx = ordered.index(requested)
        return ordered[idx:]  # keep the requested model first, then downstream
    return ordered


class LLMClient:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
        fallback_models: list[str] | None = None,
        _client: httpx.AsyncClient | None = None,
    ):
        self.model = model or settings.llm_model
        self.api_key = api_key or settings.llm_api_key
        self.base_url = base_url or settings.llm_base_url
        self.fallback_models = fallback_models or _resolve_fallback_models()
        if _client is not None:
            self._client = _client
            self._owns_client = False
        else:
            proxy_url = self._resolve_proxy()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0),
                proxy=proxy_url if proxy_url else None,
                trust_env=True,
            )
            self._owns_client = True

    def fork(self) -> "LLMClient":
        """Create a per-request fork sharing the connection pool.

        All requests share the same ``httpx.AsyncClient`` (connection pool is
        thread-safe), but each fork has independent ``model`` and
        ``fallback_models`` state — so fallback switching on one request
        never affects another.

        The fork does NOT own the client; closing it is a no-op.
        """
        return LLMClient(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            fallback_models=list(self.fallback_models) if self.fallback_models else None,
            _client=self._client,
        )

    @staticmethod
    def _resolve_proxy() -> str | None:
        """Resolve proxy URL for LLM calls. Only uses llm_proxy setting, not system env vars."""
        return getattr(settings, "llm_proxy", None) or None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _build_body(
        self,
        messages: list[Message],
        model_name: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
        tools: list[ToolDef] | None,
        tool_choice: str,
        response_format: Literal["json_object"] | None,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model_name,
            "messages": [_message_to_dict(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = [_tool_def_to_dict(t) for t in tools]
            body["tool_choice"] = tool_choice
        if response_format == "json_object":
            body["response_format"] = {"type": response_format}
        return body

    async def chat(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        stream: bool = False,
        tools: list[ToolDef] | None = None,
        tool_choice: str = "auto",
        response_format: Literal["json_object"] | None = None,
        request_id: str = "",
        session_id: str | None = None,
    ) -> ChatResult:
        resolved = _resolve_models(model or self.model)
        if self.fallback_models:
            primary = model or self.model
            models_to_try = [primary] + [m for m in self.fallback_models if m != primary]
        else:
            models_to_try = resolved
        last_error: Exception | None = None

        logger.bind(request_id=request_id).info("LLM chat | model={} | stream={}", model or self.model, stream)

        for current_model in models_to_try:
            try:
                model_def = _get_model(current_model)
            except KeyError as e:
                logger.warning("Model '{}' not registered, skipping", current_model)
                last_error = e
                continue

            body = self._build_body(
                messages, current_model, temperature, max_tokens,
                stream, tools, tool_choice, response_format,
            )
            headers = {
                "Authorization": f"Bearer {model_def.api_key}",
                "Content-Type": "application/json",
            }
            url = f"{model_def.base_url}/chat/completions"

            if not stream:
                try:
                    return await self._chat_non_streaming(url, headers, body, current_model, session_id=session_id)
                except (LLMNonRetryableError, LLMRetryExhaustedError) as e:
                    last_error = e
                    if current_model != models_to_try[-1]:
                        logger.warning("Model '{}' failed, trying next: {}", current_model, e)
                    continue

            # ---- Stream mode: accumulate from shared generator ----
            content_parts: list[str] = []
            tool_calls: dict[int, ToolCall] = {}
            finish_reason = ""
            usage: Usage | None = None

            try:
                async for chunk in self._stream_chat(url, headers, body):
                    delta = chunk.get("delta", {})

                    if delta.get("content"):
                        content_parts.append(delta["content"])

                    if delta.get("tool_calls"):
                        for tc_chunk in delta["tool_calls"]:
                            idx = tc_chunk.get("index", 0)
                            if idx not in tool_calls:
                                tool_calls[idx] = ToolCall(id="", function={"name": "", "arguments": ""})
                            if tc_chunk.get("id"):
                                tool_calls[idx].id = tc_chunk["id"]
                            if tc_chunk.get("function"):
                                fn = tc_chunk["function"]
                                if fn.get("name"):
                                    tool_calls[idx].function["name"] += fn["name"]
                                if fn.get("arguments"):
                                    tool_calls[idx].function["arguments"] += fn["arguments"]

                    if chunk.get("finish_reason"):
                        finish_reason = chunk["finish_reason"]

                    if chunk.get("usage"):
                        ud = chunk["usage"]
                        usage = Usage(
                            prompt_tokens=ud.get("prompt_tokens", 0),
                            completion_tokens=ud.get("completion_tokens", 0),
                            total_tokens=ud.get("total_tokens", 0),
                        )
                        _tracker.track(
                            model=current_model,
                            prompt_tokens=usage.prompt_tokens,
                            completion_tokens=usage.completion_tokens,
                            session_id=session_id or "",
                        )

                content = "".join(content_parts) or None
                return ChatResult(
                    content=content,
                    tool_calls=list(tool_calls.values()) or None,
                    usage=usage,
                    model=current_model,
                    finish_reason=finish_reason,
                )
            except (LLMNonRetryableError, LLMRetryExhaustedError) as e:
                last_error = e
                if current_model != models_to_try[-1]:
                    logger.warning("Model '{}' failed, trying next: {}", current_model, e)
                continue

        raise LLMError(f"All models failed; final error: {last_error}") from last_error

    async def _chat_non_streaming(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        model_name: str,
        session_id: str | None = None,
    ) -> ChatResult:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = await self._client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                return self._parse_response(data, model_name, session_id=session_id)
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in RETRYABLE_STATUSES:
                    raise LLMNonRetryableError(
                        f"LLM API error {e.response.status_code}: {_sanitize_error(e.response.text)}"
                    ) from e
                last_error = e
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
            except Exception as e:
                raise LLMError(f"Unexpected LLM error: {_sanitize_error(str(e))}") from e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2**attempt)
        raise LLMRetryExhaustedError(
            f"LLM request failed after {MAX_RETRIES} retries"
        ) from last_error

    async def _stream_chat(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> AsyncGenerator[dict, None]:
        """Shared streaming generator. Yields dicts with delta, finish_reason, usage.

        Reads the error response body while the stream is still open so the
        real API error is surfaced rather than an opaque httpx ResponseNotRead.
        """
        last_error: Exception | None = None
        yielded_any = False
        for attempt in range(MAX_RETRIES):
            try:
                async with self._client.stream(
                    "POST", url, headers=headers, json=body,
                ) as resp:
                    # Check status code manually so we can read the error
                    # body while the connection is still open.
                    if resp.status_code >= 400:
                        try:
                            body_bytes = await resp.aread()
                            err_text = body_bytes.decode(errors="replace")[:800]
                        except Exception:
                            err_text = "<unable to read response body>"
                        if resp.status_code in RETRYABLE_STATUSES:
                            last_error = LLMRetryExhaustedError(
                                f"LLM API error {resp.status_code}: {_sanitize_error(err_text)}"
                            )
                            continue  # retry
                        raise LLMNonRetryableError(
                            f"LLM API error {resp.status_code}: {_sanitize_error(err_text)}"
                        )

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        choice = choices[0] if choices else {}
                        yielded_any = True
                        yield {
                            "delta": choice.get("delta", {}),
                            "finish_reason": choice.get("finish_reason"),
                            "usage": chunk.get("usage"),
                        }
                    return
            except (LLMNonRetryableError, LLMRetryExhaustedError):
                raise
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if yielded_any:
                    raise LLMError(
                        "Stream interrupted after partial output; some content may have been lost"
                    ) from e
                last_error = e
            except Exception as e:
                raise LLMError(f"Unexpected LLM error: {_sanitize_error(str(e))}") from e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2**attempt)
        raise LLMRetryExhaustedError(
            f"LLM stream failed after {MAX_RETRIES} retries"
        ) from last_error

    async def chat_stream_tokens(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        tools: list[ToolDef] | None = None,
        request_id: str = "",
        session_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream tokens from the LLM one by one. Yields content strings.

        If *tools* is provided, ``tool_choice`` is set to ``"none"`` so the
        model sees tool definitions but is forced to respond in plain text.
        """
        models_to_try = _resolve_models(model or self.model)
        last_error: Exception | None = None

        logger.bind(request_id=request_id).info("LLM stream | model={}", model or self.model)

        for current_model in models_to_try:
            try:
                model_def = _get_model(current_model)
            except KeyError as e:
                logger.warning("Model '{}' not registered, skipping", current_model)
                last_error = e
                continue

            body = self._build_body(
                messages, current_model, temperature, max_tokens,
                stream=True, tools=tools,
                tool_choice="none" if tools else "auto",
                response_format=None,
            )
            headers = {
                "Authorization": f"Bearer {model_def.api_key}",
                "Content-Type": "application/json",
            }
            url = f"{model_def.base_url}/chat/completions"

            try:
                async for chunk in self._stream_chat(url, headers, body):
                    if chunk.get("usage"):
                        ud = chunk["usage"]
                        _tracker.track(
                            model=current_model,
                            prompt_tokens=ud.get("prompt_tokens", 0),
                            completion_tokens=ud.get("completion_tokens", 0),
                            session_id=session_id or "",
                        )
                    delta = chunk.get("delta", {})
                    if delta.get("content"):
                        yield delta["content"]
                return
            except (LLMNonRetryableError, LLMRetryExhaustedError, LLMError) as e:
                last_error = e
                if current_model != models_to_try[-1]:
                    logger.warning("Model '{}' failed, trying next: {}", current_model, e)
                continue

        raise LLMError(f"All models failed; final error: {last_error}") from last_error

    async def chat_stream_with_tools(
        self,
        messages: list[Message],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        tools: list[ToolDef] | None = None,
        tool_choice: str = "auto",
        request_id: str = "",
        session_id: str | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream tokens AND tool calls from the LLM.

        Yields :class:`StreamChunk` objects as they arrive from the API:
        - content chunks (text tokens)
        - tool_call chunks (function name and arguments streaming in)
        - final chunk with finish_reason and usage

        This enables executing tools during streaming rather than after.
        """
        models_to_try = _resolve_models(model or self.model)
        last_error: Exception | None = None

        logger.bind(request_id=request_id).info(
            "LLM stream+tool | model=%s tools=%d",
            model or self.model, len(tools) if tools else 0,
        )

        for current_model in models_to_try:
            try:
                model_def = _get_model(current_model)
            except KeyError as e:
                logger.warning("Model '{}' not registered, skipping", current_model)
                last_error = e
                continue

            body = self._build_body(
                messages, current_model, temperature, max_tokens,
                stream=True, tools=tools, tool_choice=tool_choice,
                response_format=None,
            )
            headers = {
                "Authorization": f"Bearer {model_def.api_key}",
                "Content-Type": "application/json",
            }
            url = f"{model_def.base_url}/chat/completions"

            # Accumulators for streaming tool calls
            tool_call_builders: dict[int, dict] = {}  # index -> {id, function: {name, arguments}}
            reasoning_parts: list[str] = []

            try:
                async for chunk in self._stream_chat(url, headers, body):
                    if chunk.get("usage"):
                        ud = chunk["usage"]
                        _tracker.track(
                            model=current_model,
                            prompt_tokens=ud.get("prompt_tokens", 0),
                            completion_tokens=ud.get("completion_tokens", 0),
                            session_id=session_id or "",
                        )

                    delta = chunk.get("delta", {})
                    finish_reason = chunk.get("finish_reason")

                    # Handle tool call deltas
                    if delta.get("tool_calls"):
                        for tc_chunk in delta["tool_calls"]:
                            idx = tc_chunk.get("index", 0)
                            if idx not in tool_call_builders:
                                tool_call_builders[idx] = {
                                    "id": "",
                                    "function": {"name": "", "arguments": ""},
                                }
                            builder = tool_call_builders[idx]
                            if tc_chunk.get("id"):
                                builder["id"] += tc_chunk["id"]
                            if tc_chunk.get("function"):
                                fn = tc_chunk["function"]
                                if fn.get("name"):
                                    builder["function"]["name"] += fn["name"]
                                if fn.get("arguments"):
                                    builder["function"]["arguments"] += fn["arguments"]

                    # When finish_reason signals the end of a tool call stream,
                    # emit ALL tool calls with their fully accumulated arguments.
                    # (Previously we emitted during streaming, but DeepSeek sends
                    # arguments incrementally — the first chunk may have only "{".
                    # Emitting mid-stream causes `loop.py` to receive incomplete JSON
                    # and parse it as empty `{}`. We now wait for finish_reason so
                    # every tool call carries complete arguments.)
                    if finish_reason:
                        for idx in sorted(tool_call_builders.keys()):
                            builder = tool_call_builders[idx]
                            if builder["function"]["name"]:
                                yield StreamChunk(
                                    tool_call=ToolCall(
                                        id=builder["id"],
                                        function=builder["function"],
                                    ),
                                )
                        tool_call_builders.clear()

                        usage = None
                        if chunk.get("usage"):
                            ud = chunk["usage"]
                            usage = Usage(
                                prompt_tokens=ud.get("prompt_tokens", 0),
                                completion_tokens=ud.get("completion_tokens", 0),
                                total_tokens=ud.get("total_tokens", 0),
                            )
                        reasoning_text = "".join(reasoning_parts) or None
                        yield StreamChunk(
                            finish_reason=finish_reason,
                            usage=usage,
                            reasoning_content=reasoning_text,
                        )

                    # Capture reasoning_content for DeepSeek thinking mode
                    if delta.get("reasoning_content"):
                        reasoning_parts.append(delta["reasoning_content"])

                    # Yield content chunks immediately
                    if delta.get("content"):
                        yield StreamChunk(content=delta["content"])

                return
            except (LLMNonRetryableError, LLMRetryExhaustedError, LLMError) as e:
                last_error = e
                if current_model != models_to_try[-1]:
                    logger.warning("Model '{}' failed, trying next: {}", current_model, e)
                continue

        raise LLMError(f"All models failed; final error: {last_error}") from last_error

    def _parse_response(self, data: dict, model_name: str, session_id: str | None = None) -> ChatResult:
        choice = data["choices"][0]
        msg = choice.get("message", {})
        content = msg.get("content")

        tool_calls: list[ToolCall] | None = None
        if msg.get("tool_calls"):
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    type=tc.get("type", "function"),
                    function=tc.get("function"),
                )
                for tc in msg["tool_calls"]
            ]

        usage_data = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        _tracker.track(
            model=model_name,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            session_id=session_id or "",
        )

        return ChatResult(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=model_name,
            finish_reason=choice.get("finish_reason", ""),
        )
