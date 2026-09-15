"""Shared fixtures for agent tests."""
import pytest
from unittest.mock import AsyncMock
from app.llm.types import Message, ChatResult, Usage


@pytest.fixture
def mock_llm_client():
    client = AsyncMock()
    client.chat = AsyncMock(return_value=ChatResult(
        content='{"intent": "simple_q", "reason": "test"}',
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    ))
    client.chat_stream_tokens = AsyncMock()
    return client


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    return redis


@pytest.fixture
def sample_messages():
    return [
        Message(role="system", content="You are a writing assistant."),
        Message(role="user", content="Hello"),
        Message(role="assistant", content="Hi there!"),
    ]