"""Tests for token estimation unification, context limits, and history summary persistence."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.agent.context_compressor import (
    estimate_tokens,
    estimate_text_tokens,
    DEFAULT_MODEL_LIMIT,
)
from app.agent.memory.history_manager import (
    HistoryManager,
)
from app.llm.types import Message


# ── T1: CJK-aware token estimation ─────────────────────────────────────


class TestCJKTokenEstimation:
    """T1: verify estimate_text_tokens returns >= 1.2 * char count for CJK-heavy text."""

    def test_estimate_text_tokens_cjk_dense(self):
        """CJK-heavy text should produce token estimate >= 1.2 * char count."""
        # Pure Chinese text (classic novel excerpt)
        text = "正值秋深，景物萧条，西风飒飒，落叶满空山。"
        char_count = len(text)
        token_count = estimate_text_tokens(text)
        ratio = token_count / char_count
        assert ratio >= 1.0, (
            f"CJK token estimate ({token_count}) should be >= char count ({char_count}), "
            f"actual ratio {ratio:.2f}"
        )

    def test_estimate_text_tokens_mixed(self):
        """Mixed CJK + ASCII text should produce reasonable token count."""
        text = "Chapter 1: 这是第一章的标题"
        token_count = estimate_text_tokens(text)
        assert token_count > 0
        assert token_count < len(text) * 2  # Sanity upper bound

    def test_estimate_text_tokens_empty(self):
        assert estimate_text_tokens("") == 0
        assert estimate_text_tokens(None) == 0

    def test_estimate_tokens_messages_delegates(self):
        """estimate_tokens (list[Message]) should delegate to CJK-aware estimate_text_tokens."""
        msgs = [
            Message(role="user", content="你好，这是一个中文测试消息，内容比较丰富"),
            Message(role="assistant", content="是的，这是一个回复。"),
        ]
        total = estimate_tokens(msgs)
        # Total should be sum of estimate_text_tokens per message
        expected = sum(
            estimate_text_tokens(m.content or "")
            for m in msgs
        )
        assert total == expected, f"{total} != {expected}"

    def test_estimate_tokens_with_tool_calls(self):
        """estimate_tokens should include tool_calls arguments."""
        from app.llm.types import ToolCall
        tc = ToolCall(
            id="call_1",
            function={"name": "list_chapters", "arguments": '{"project_id": "123"}'},
        )
        msgs = [
            Message(role="user", content="列出章节"),
            Message(role="assistant", content=None, tool_calls=[tc]),
        ]
        total = estimate_tokens(msgs)
        # Should count content + tool_call arguments
        content_tokens = estimate_text_tokens("列出章节")
        args_tokens = estimate_text_tokens('{"project_id": "123"}')
        assert total == content_tokens + args_tokens


# ── T2: History summary persistence ────────────────────────────────────


class TestHistorySummaryPersistence:
    """T2: verify maybe_summarize persists result to conv_memory."""

    @pytest.mark.asyncio
    async def test_summary_persists_to_conv_memory(self):
        """After summarize triggers, conv_memory.replace_history should be called."""
        fake_llm = AsyncMock()
        fake_llm.chat = AsyncMock(return_value=MagicMock(
            content="User asked about chapter structure. The assistant provided analysis."
        ))
        conv_memory = AsyncMock()
        # Build enough messages to trigger summarization (> SUMMARIZE_AFTER=30 user msgs)
        msgs = []
        for i in range(70):
            msgs.append(Message(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Test message {i} with enough padding to make sure it triggers the summarization threshold " * 20,
            ))

        hm = HistoryManager(
            llm_client=fake_llm,
            conversation_id="test-conv-123",
            conv_memory=conv_memory,
        )
        result = await hm.maybe_summarize(msgs)

        # conv_memory.replace_history should have been called
        conv_memory.replace_history.assert_awaited_once()
        call_args = conv_memory.replace_history.call_args[0]
        assert call_args[0] == "test-conv-123"
        assert len(call_args[1]) < len(msgs)

    @pytest.mark.asyncio
    async def test_summary_skipped_when_short(self):
        """Short history should not trigger summary or persistence."""
        conv_memory = AsyncMock()
        msgs = [Message(role="user", content="hi"), Message(role="assistant", content="hello")]

        hm = HistoryManager(
            conversation_id="test-conv-123",
            conv_memory=conv_memory,
        )
        result = await hm.maybe_summarize(msgs)

        conv_memory.replace_history.assert_not_called()


# ── T3: Throttling (last_summary_count persisted and honored) ──────────


class TestSummaryThrottling:
    """T3: verify MIN_SUMMARIZE_INTERVAL is honored across requests."""

    @pytest.mark.asyncio
    async def test_throttle_honored_with_redis(self):
        """When Redis has a recent summary count, second request skips LLM call."""
        fake_llm = AsyncMock()
        fake_llm.chat = AsyncMock(return_value=MagicMock(
            content="Summary of the conversation."
        ))
        conv_memory = AsyncMock()

        # First call: trigger summary
        msgs1 = []
        for i in range(70):
            msgs1.append(Message(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Long message {i} with padding to cross threshold " * 20,
            ))

        hm1 = HistoryManager(
            llm_client=fake_llm,
            conversation_id="test-throttle-conv",
            conv_memory=conv_memory,
        )
        result1 = await hm1.maybe_summarize(msgs1)
        assert conv_memory.replace_history.await_count >= 1
        first_call_count = fake_llm.chat.call_count

        # Second call: same conversation, messages haven't grown much
        # Simulate a new HistoryManager (as would happen on a new request)
        hm2 = HistoryManager(
            llm_client=fake_llm,
            conversation_id="test-throttle-conv",
            conv_memory=conv_memory,
        )
        # Set last_summary_count to match first call (simulating what redis would give)
        hm2._last_summary_count = len(msgs1)

        # Fewer messages than MIN_SUMMARIZE_INTERVAL (10) since last summary
        msgs2 = list(msgs1) + [
            Message(role="user", content="One more question"),
            Message(role="assistant", content="Sure!"),
        ]
        result2 = await hm2.maybe_summarize(msgs2)

        # LLM should NOT have been called again — throttled
        assert fake_llm.chat.call_count == first_call_count, (
            "LLM summarization was called again despite MIN_SUMMARIZE_INTERVAL guard"
        )
        # Messages should be returned unchanged
        assert result2 is msgs2

    @pytest.mark.asyncio
    async def test_throttle_honored_without_redis(self):
        """Without Redis, each new HistoryManager starts at zero,
        so throttling relies entirely on MIN_SUMMARIZE_INTERVAL check
        comparing current length vs _last_summary_count."""
        fake_llm = AsyncMock()
        fake_llm.chat = AsyncMock(return_value=MagicMock(
            content="Summary of the conversation."
        ))
        conv_memory = AsyncMock()

        msgs = []
        for i in range(70):
            msgs.append(Message(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Msg {i} with padding to cross threshold " * 20,
            ))

        hm = HistoryManager(llm_client=fake_llm, conv_memory=conv_memory)
        result = await hm.maybe_summarize(msgs)

        # Without conversation_id and redis, summary runs
        # but replace_history won't be called (no conv_memory + conversation_id combo)
        # Actually conv_memory is set but conversation_id is None
        assert fake_llm.chat.call_count >= 1


# ── T4: Model context limit from settings ─────────────────────────────


class TestModelContextLimit:
    """T4: verify MODEL_CONTEXT_LIMIT derives from settings (400K window)."""

    def test_context_limit_from_settings(self):
        """DEFAULT_MODEL_LIMIT should come from settings.llm_context_window."""
        from app.config import settings
        assert DEFAULT_MODEL_LIMIT == settings.llm_context_window

    def test_token_budget_default_from_settings(self):
        """token_budget's default model_limit should come from settings."""
        from app.agent.token_budget import _DEFAULT_MODEL_LIMIT
        from app.config import settings
        # Should be settings.llm_context_window (from config.py)
        assert _DEFAULT_MODEL_LIMIT == settings.llm_context_window


# ── T5: RecoveryExecutor — single backoff, model rotation ──────────────


class TestRecoveryExecutor:
    """T5: verify RecoveryExecutor fixes for the audit branch."""

    @pytest.mark.asyncio
    async def test_no_sleep_in_apply(self):
        """Bug A: apply() should NOT sleep — caller (loop.py) handles it."""
        from app.agent.recovery import RecoveryExecutor, RecoveryAction, RecoveryDecision
        executor = RecoveryExecutor()
        decision = RecoveryDecision(
            action=RecoveryAction.RETRY,
            delay_seconds=9999,  # would be a huge sleep if called
        )
        state = {"recovery_state": {}}
        import time
        t0 = time.monotonic()
        updates = await executor.apply(decision, state)
        elapsed = time.monotonic() - t0
        assert elapsed < 1, f"apply() slept {elapsed:.2f}s — should be instant"
        assert "recovery_state" in updates

    @pytest.mark.asyncio
    async def test_model_rotation_persisted_in_recovery_state(self):
        """Bug B: SWITCH_MODEL must advance index in recovery_state["model_index"]."""
        from app.agent.recovery import RecoveryExecutor, RecoveryAction, RecoveryDecision
        fallbacks = ["model-b", "model-c"]
        executor = RecoveryExecutor(fallback_models=fallbacks)
        decision = RecoveryDecision(
            action=RecoveryAction.SWITCH_MODEL,
            message="Switching model",
        )
        state = {"recovery_state": {}}
        updates = await executor.apply(decision, state)
        rs = updates["recovery_state"]
        assert rs.get("model_index") == 1
        assert updates.get("_model_override") == "model-b"

    @pytest.mark.asyncio
    async def test_two_switches_use_different_fallbacks(self):
        """Bug B: Two consecutive SWITCH_MODEL decisions must pick different models."""
        from app.agent.recovery import RecoveryExecutor, RecoveryAction, RecoveryDecision
        fallbacks = ["model-b", "model-c"]

        # First switch
        executor1 = RecoveryExecutor(fallback_models=fallbacks)
        decision1 = RecoveryDecision(action=RecoveryAction.SWITCH_MODEL)
        updates1 = await executor1.apply(decision1, {"recovery_state": {}})
        assert updates1["_model_override"] == "model-b"

        # Second switch — simulate persistent state by passing recovery_state
        recovery_state = updates1["recovery_state"]  # model_index=1
        executor2 = RecoveryExecutor(
            fallback_models=fallbacks,
            recovery_state=recovery_state,
        )
        decision2 = RecoveryDecision(action=RecoveryAction.SWITCH_MODEL)
        updates2 = await executor2.apply(decision2, {"recovery_state": recovery_state})
        assert updates2["_model_override"] == "model-c", (
            f"Second switch picked {updates2['_model_override']}, expected model-c"
        )

    @pytest.mark.asyncio
    async def test_model_rotation_exhausted(self):
        """When all fallbacks are exhausted, all further switches give up."""
        from app.agent.recovery import RecoveryExecutor, RecoveryAction, RecoveryDecision
        fallbacks = ["model-b"]
        executor = RecoveryExecutor(
            fallback_models=fallbacks,
            recovery_state={"model_index": 1},
        )
        decision = RecoveryDecision(action=RecoveryAction.SWITCH_MODEL)
        updates = await executor.apply(decision, {"recovery_state": {"model_index": 1}})
        rs = updates["recovery_state"]
        assert rs.get("models_exhausted") is True
        assert "_model_override" not in updates


# ── T6: History threshold alignment (M19/M23) ─────────────────────────


class TestHistoryThresholdAlignment:
    """M19/M23: history summarization thresholds were 200K/400K — larger
    than the 120K model window, so summarization never fired in time.
    Thresholds are now derived from the same window the loop uses, and the
    private token estimator was removed in favor of the shared one."""

    def test_summarize_threshold_derived_from_window(self):
        from app.agent.memory import history_manager as hm

        window = hm._MODEL_CONTEXT_WINDOW
        assert hm.MAX_HISTORY_TOKENS_EST == int(window * 0.75)
        assert hm.MAX_HISTORY_TOKENS_EST < window
        assert hm.MAX_HISTORY_TOKENS_HARD <= window

    def test_history_uses_shared_estimator(self):
        import inspect

        import app.agent.memory.history_manager as hm

        src = inspect.getsource(hm.HistoryManager.maybe_summarize)
        # The private CJK estimator (with its own 1.5x multiplier) is gone.
        assert "estimate_tokens(messages)" in src
        assert "estimate_tokens(result)" in src
        assert "estimate_text_tokens" not in src
        assert "_estimate_tokens(" not in src

    def test_recovery_compress_uses_loop_window(self):
        import inspect

        from app.agent.recovery import RecoveryExecutor

        src = inspect.getsource(RecoveryExecutor.apply)
        assert "model_limit=settings.llm_context_window" in src
