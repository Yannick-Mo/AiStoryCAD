"""Tests for conversation history management."""

import pytest
from app.agent.memory.history_manager import HistoryManager
from app.llm.types import Message


class FakeLLMClient:
    async def chat(self, messages, **kwargs):
        class FakeResult:
            content = "User asked about chapter structure. The assistant provided analysis."
        return FakeResult()


@pytest.fixture
def manager():
    return HistoryManager(llm_client=FakeLLMClient())


@pytest.mark.asyncio
class TestHistoryManager:
    async def test_short_history_no_summary(self, manager):
        msgs = [Message(role="user", content="hi"), Message(role="assistant", content="hello")]
        result = await manager.maybe_summarize(msgs)
        assert len(result) >= 2

    async def test_long_history_triggers_summary(self, manager):
        # maybe_summarize triggers when user-message count exceeds
        # SUMMARIZE_AFTER (30), so build 70 alternating messages (35 user).
        msgs = []
        for i in range(70):
            msgs.append(Message(role="user" if i % 2 == 0 else "assistant", content=f"Message {i} with lots of padding to make it long enough to trigger summarization " * 15))
        result = await manager.maybe_summarize(msgs)
        has_summary = any("[至此的对话摘要]" in (m.content or "") for m in result)
        assert has_summary or len(result) < len(msgs)

    async def test_summarize_old_returns_string(self, manager):
        msgs = []
        for i in range(30):
            msgs.append(Message(role="user" if i % 2 == 0 else "assistant", content=f"Message {i}" * 10))
        summary = await manager._summarize_old(msgs)
        assert isinstance(summary, str)
        assert len(summary) > 0
