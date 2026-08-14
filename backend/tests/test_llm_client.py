"""Tests for LLM client infrastructure."""

import json

import httpx
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.llm.client import (
    LLMClient,
    LLMError,
    LLMNonRetryableError,
    LLMRetryExhaustedError,
)
from app.llm.registry import register, _registry as _model_registry
from app.llm.types import Message, ModelDef, ToolCall, ToolDef


@pytest.fixture
def client():
    with patch("app.llm.client.settings") as mock_settings, \
         patch("app.llm.client.httpx.AsyncClient") as mock_http_cls:
        mock_settings.llm_api_key = "test-key"
        mock_settings.llm_base_url = "https://api.test.com/v1"
        mock_http = AsyncMock()
        mock_http_cls.return_value = mock_http
        c = LLMClient(model="test-model")
        c._client = mock_http
        yield c


class TestLLMClientBuildBody:
    def test_basic_body(self, client):
        msgs = [Message(role="user", content="hello")]
        body = client._build_body(msgs, "m1", 0.7, 4096, False, None, "auto", None)
        assert body["model"] == "m1"
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 4096
        assert "stream" not in body

    def test_stream_body(self, client):
        msgs = [Message(role="user", content="hello")]
        body = client._build_body(msgs, "m1", 0.7, 4096, True, None, "auto", None)
        assert body["stream"] is True

    def test_tools_in_body(self, client):
        msgs = [Message(role="user", content="hello")]
        tools = [ToolDef(type="function", function={"name": "test_func"})]
        body = client._build_body(msgs, "m1", 0.7, 4096, False, tools, "auto", None)
        assert "tools" in body
        assert body["tools"][0]["function"]["name"] == "test_func"

    def test_json_response_format(self, client):
        msgs = [Message(role="user", content="hello")]
        body = client._build_body(msgs, "m1", 0.7, 4096, False, None, "auto", "json_object")
        assert body["response_format"]["type"] == "json_object"


class TestLLMClientChatNonStreaming:
    def _make_resp(self, status_code, json_data):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = json_data
        mock_resp.text = json.dumps(json_data)
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @pytest.mark.asyncio
    async def test_successful_response(self, client):
        resp = self._make_resp(200, {
            "choices": [{
                "message": {"content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        client._client.post = AsyncMock(return_value=resp)

        result = await client._chat_non_streaming(
            "https://api.test.com/chat/completions",
            {"Authorization": "Bearer test-key"},
            {"model": "test", "messages": []},
            "test-model",
        )
        assert result.content == "Hello!"
        assert result.usage.prompt_tokens == 10

    @pytest.mark.asyncio
    async def test_retry_on_429(self, client):
        mock_429_resp = self._make_resp(429, {})
        mock_429_resp.raise_for_status.side_effect = __import__("httpx").HTTPStatusError(
            "Too Many", request=MagicMock(), response=mock_429_resp
        )
        mock_200_resp = self._make_resp(200, {
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

        client._client.post = AsyncMock(side_effect=[mock_429_resp, mock_200_resp])

        result = await client._chat_non_streaming(
            "https://api.test.com/chat/completions",
            {"Authorization": "Bearer test-key"},
            {"model": "test", "messages": []},
            "test-model",
        )
        assert result.content == "OK"

    @pytest.mark.asyncio
    async def test_non_retryable_status(self, client):
        mock_400_resp = self._make_resp(400, {})
        mock_400_resp.raise_for_status.side_effect = __import__("httpx").HTTPStatusError(
            "Bad Request", request=MagicMock(), response=mock_400_resp
        )

        client._client.post = AsyncMock(return_value=mock_400_resp)

        with pytest.raises(LLMError, match="400"):
            await client._chat_non_streaming(
                "https://api.test.com/chat/completions",
                {"Authorization": "Bearer test-key"},
                {"model": "test", "messages": []},
                "test-model",
            )


class TestLLMClientParseResponse:
    def test_parse_content(self, client):
        data = {
            "choices": [{
                "message": {"content": "Hello world"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        result = client._parse_response(data, "test-model")
        assert result.content == "Hello world"
        assert result.model == "test-model"
        assert result.finish_reason == "stop"

    def test_parse_tool_calls(self, client):
        data = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "test_tool", "arguments": '{"key":"val"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        result = client._parse_response(data, "test-model")
        assert result.content is None
        assert result.tool_calls is not None
        assert result.tool_calls[0].function["name"] == "test_tool"

    def test_parse_no_content(self, client):
        data = {
            "choices": [{"message": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        result = client._parse_response(data, "test-model")
        assert result.content is None
        assert result.tool_calls is None


class TestLLMClientFallback:
    """Tests for model fallback in chat()."""

    @pytest.fixture(autouse=True)
    def cleanup_registry(self):
        _model_registry.clear()
        yield
        _model_registry.clear()

    def _make_resp(self, status_code, json_data):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = json_data
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def _make_error_resp(self, status_code):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = f"error {status_code}"
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp,
        )
        return resp

    @pytest.fixture
    def fallback_client(self):
        register("primary", ModelDef(api_key="key1", base_url="https://api1.test/v1"))
        register("secondary", ModelDef(api_key="key2", base_url="https://api2.test/v1"))

        with patch("app.llm.client.settings") as mock_settings, \
             patch("app.llm.client.httpx.AsyncClient") as mock_http_cls:
            mock_settings.llm_api_key = "test-key"
            mock_settings.llm_base_url = "https://api.test.com/v1"
            mock_http = AsyncMock()
            mock_http_cls.return_value = mock_http
            c = LLMClient(model="primary", fallback_models=["secondary"])
            c._client = mock_http
            yield c

    @pytest.mark.asyncio
    async def test_primary_succeeds_no_fallback(self, fallback_client):
        """Primary works first time, no fallback needed."""
        resp = self._make_resp(200, {
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        fallback_client._client.post = AsyncMock(return_value=resp)

        result = await fallback_client.chat(messages=[Message(role="user", content="hi")])
        assert result.content == "OK"
        assert result.model == "primary"

    @pytest.mark.asyncio
    async def test_fallback_on_retry_exhausted(self, fallback_client):
        """Primary gives 3 retryable errors, secondary succeeds."""
        err_429 = self._make_error_resp(429)
        ok_resp = self._make_resp(200, {
            "choices": [{"message": {"content": "fallback OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        })
        # 3 failures on primary, then success on secondary
        fallback_client._client.post = AsyncMock(side_effect=[err_429, err_429, err_429, ok_resp])

        result = await fallback_client.chat(messages=[Message(role="user", content="hi")])
        assert result.content == "fallback OK"
        assert result.model == "secondary"

    @pytest.mark.asyncio
    async def test_fallback_on_non_retryable(self, fallback_client):
        """Primary returns 400 (skips retries), secondary succeeds."""
        err_400 = self._make_error_resp(400)
        ok_resp = self._make_resp(200, {
            "choices": [{"message": {"content": "fallback OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        })
        fallback_client._client.post = AsyncMock(side_effect=[err_400, ok_resp])

        result = await fallback_client.chat(messages=[Message(role="user", content="hi")])
        assert result.content == "fallback OK"
        assert result.model == "secondary"

    @pytest.mark.asyncio
    async def test_all_models_fail(self, fallback_client):
        """Both primary and secondary exhaust retries -> LLMError."""
        err_503 = self._make_error_resp(503)
        # primary: 3 attempts, secondary: 3 attempts = 6 total
        fallback_client._client.post = AsyncMock(side_effect=[err_503] * 6)

        with pytest.raises(LLMError, match="All models failed"):
            await fallback_client.chat(messages=[Message(role="user", content="hi")])

    @pytest.mark.asyncio
    async def test_fallback_model_not_registered_skipped(self, fallback_client):
        """Secondary not registered -> skip to tertiary."""
        register("tertiary", ModelDef(api_key="key3", base_url="https://api3.test/v1"))
        fallback_client.fallback_models = ["unknown", "tertiary"]

        err_429 = self._make_error_resp(429)
        ok_resp = self._make_resp(200, {
            "choices": [{"message": {"content": "tertiary OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        # primary 3 failures, unknown skipped, tertiary succeeds
        fallback_client._client.post = AsyncMock(side_effect=[err_429, err_429, err_429, ok_resp])

        result = await fallback_client.chat(messages=[Message(role="user", content="hi")])
        assert result.content == "tertiary OK"
        assert result.model == "tertiary"

    @pytest.mark.asyncio
    async def test_fatal_402_no_fallback_no_retries(self, fallback_client):
        """402 (insufficient balance) is account-level: no retry, no fallback."""
        err_402 = self._make_error_resp(402)
        ok_resp = self._make_resp(200, {
            "choices": [{"message": {"content": "should never be used"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
        # If fallback happened, the secondary would be called → ok_resp used
        fallback_client._client.post = AsyncMock(side_effect=[err_402, ok_resp])

        with pytest.raises(LLMError, match="402"):
            await fallback_client.chat(messages=[Message(role="user", content="hi")])
        assert fallback_client._client.post.call_count == 1  # never touched secondary

    @pytest.mark.asyncio
    async def test_fatal_401_stream_no_fallback(self, fallback_client):
        """401 during streaming is also fatal: propagates without fallback."""
        mock_stream = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.aread = AsyncMock(return_value=b'{"error":{"message":"Unauthorized"}}')
        mock_stream.__aenter__.return_value = mock_resp
        fallback_client._client.stream = MagicMock(return_value=mock_stream)

        with pytest.raises(LLMError, match="401"):
            await fallback_client.chat(
                messages=[Message(role="user", content="hi")], stream=True,
            )
        assert fallback_client._client.stream.call_count == 1  # no second model


class TestMessageType:
    def test_message_creation(self):
        msg = Message(role="user", content="test")
        assert msg.role == "user"
        assert msg.content == "test"

    def test_message_to_dict(self):
        msg = Message(role="user", content="hello")
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "hello"

    def test_message_with_tool_call(self):
        tc = ToolCall(id="call_1", type="function", function={"name": "fn"})
        msg = Message(role="assistant", content=None, tool_calls=[tc])
        assert msg.tool_calls is not None
        assert msg.tool_calls[0].id == "call_1"

    def test_tool_response_message(self):
        msg = Message(role="tool", content='{"result":"ok"}', tool_call_id="call_1")
        assert msg.tool_call_id == "call_1"
        assert msg.content == '{"result":"ok"}'


class TestLLMClientStreamChat:
    """Tests for _stream_chat robustness."""

    @pytest.mark.asyncio
    async def test_partial_stream_raises_llm_error(self, client):
        """After yielding content, a mid-stream timeout raises LLMError without retrying."""
        async def mock_lines():
            yield 'data: {"choices":[{"delta":{"content":"Hello"}}]}'
            raise httpx.TimeoutException("Connection timed out")

        resp = AsyncMock()
        resp.status_code = 200
        resp.aiter_lines = mock_lines

        cm = MagicMock()
        cm.__aenter__.return_value = resp
        cm.__aexit__.return_value = None
        client._client.stream = MagicMock(return_value=cm)

        chunks = []
        with pytest.raises(LLMError, match="partial output"):
            async for chunk in client._stream_chat("url", {}, {"model": "test"}):
                chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0]["delta"].get("content") == "Hello"

    @pytest.mark.asyncio
    async def test_no_yield_retries(self, client):
        """When no chunk was yielded, a timeout is retried."""
        async def mock_lines():
            yield 'data: {"choices":[{"delta":{"content":"Hello"}}]}'
            yield 'data: [DONE]'

        resp = AsyncMock()
        resp.status_code = 200
        resp.aiter_lines = mock_lines

        cm = MagicMock()
        cm.__aenter__.return_value = resp
        cm.__aexit__.return_value = None

        # First call raises ConnectError, second succeeds
        client._client.stream = MagicMock(
            side_effect=[httpx.ConnectError("reset"), cm]
        )

        chunks = []
        async for chunk in client._stream_chat("url", {}, {"model": "test"}):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0]["delta"]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_usage_only_chunk(self, client):
        """A chunk with empty choices but usage must not raise IndexError."""
        async def mock_lines():
            yield 'data: {"choices":[{"delta":{"content":"Hi"}}]}'
            yield 'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}'
            yield "data: [DONE]"

        resp = AsyncMock()
        resp.status_code = 200
        resp.aiter_lines = mock_lines

        cm = MagicMock()
        cm.__aenter__.return_value = resp
        cm.__aexit__.return_value = None
        client._client.stream = MagicMock(return_value=cm)

        chunks = []
        async for chunk in client._stream_chat("url", {}, {"model": "test"}):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[1].get("usage") == {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        }


class TestLLMClientStreamOptions:
    def test_build_body_includes_include_usage(self, client):
        msgs = [Message(role="user", content="hello")]
        body = client._build_body(msgs, "m1", 0.7, 4096, True, None, "auto", None)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}

    def test_non_stream_no_stream_options(self, client):
        msgs = [Message(role="user", content="hello")]
        body = client._build_body(msgs, "m1", 0.7, 4096, False, None, "auto", None)
        assert "stream" not in body
        assert "stream_options" not in body


class TestLLMClientTimeout:
    def test_granular_timeout_on_construction(self):
        with patch("app.llm.client.settings") as mock_settings, \
             patch("app.llm.client.httpx.AsyncClient") as mock_http_cls:
            mock_settings.llm_api_key = "key"
            mock_settings.llm_base_url = "https://api.test/v1"
            mock_http = AsyncMock()
            mock_http_cls.return_value = mock_http
            LLMClient()

        kwargs = mock_http_cls.call_args.kwargs
        timeout = kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == 10.0
        assert timeout.read == 120.0
        assert timeout.write == 30.0
        assert timeout.pool == 10.0


class TestLLMClientSessionId:
    """session_id is threaded through to _tracker.track."""

    def test_session_id_in_parse_response(self, client):
        data = {
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        with patch("app.llm.client._tracker") as mock_tracker:
            client._parse_response(data, "test-model", session_id="my-session")

        mock_tracker.track.assert_called_once_with(
            model="test-model",
            prompt_tokens=5,
            completion_tokens=3,
            session_id="my-session",
        )

    def test_session_id_defaults_to_empty(self, client):
        data = {
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        with patch("app.llm.client._tracker") as mock_tracker:
            client._parse_response(data, "test-model")

        mock_tracker.track.assert_called_once_with(
            model="test-model",
            prompt_tokens=1,
            completion_tokens=2,
            session_id="",
        )

    @pytest.mark.asyncio
    async def test_chat_passes_session_id_non_streaming(self, client):
        """session_id flows from chat() through _chat_non_streaming to _parse_response to tracker."""
        register("test-m", ModelDef(api_key="key1", base_url="https://api1.test/v1"))

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
        }
        resp.raise_for_status = MagicMock()
        client._client.post = AsyncMock(return_value=resp)

        with patch("app.llm.client._tracker") as mock_tracker:
            result = await client.chat(
                messages=[Message(role="user", content="hi")],
                model="test-m",
                stream=False,
                session_id="from-chat",
            )

        assert result.content == "OK"
        mock_tracker.track.assert_called_once_with(
            model="test-m",
            prompt_tokens=2,
            completion_tokens=4,
            session_id="from-chat",
        )