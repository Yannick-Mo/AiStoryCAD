"""Tests for plan confirmation — exact-match keyword detection and the
confirm-break path of the autonomous loop.

Covers two bugs:
1. NameError: ``active_model`` / ``assistant_text_parts`` were only bound
   inside the while-loop body, but the plan-confirm branch ``break``s out
   before reaching those assignments — the final-generation phase then
   crashed with NameError.
2. Substring matching mis-detected messages like "开始写第二章" (contains
   confirm keyword "开始") or "换个开始" (contains both a confirm and a
   reject keyword) as plan confirmations.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.loop import _detect_plan_decision, autonomous_loop
from app.llm.types import Message


class TestExactMatchDecision:
    """_detect_plan_decision must use whole-message equality, not substrings."""

    @pytest.fixture
    def sample_plan(self):
        return {"steps": [{"tool": "create_character", "description": "创建角色"}]}

    def test_substring_confirm_word_is_not_confirm(self, sample_plan):
        # "开始写第二章" contains "开始" but is a new instruction, not a confirmation
        assert _detect_plan_decision("开始写第二章", sample_plan) != "confirm"

    def test_mixed_confirm_and_reject_words_is_not_confirm(self, sample_plan):
        # "换个开始" contains confirm word "开始" AND reject word "换个";
        # it must never be treated as a confirmation
        assert _detect_plan_decision("换个开始", sample_plan) != "confirm"

    def test_exact_confirm_words(self, sample_plan):
        assert _detect_plan_decision("确认", sample_plan) == "confirm"
        assert _detect_plan_decision("好的", sample_plan) == "confirm"
        assert _detect_plan_decision("开始", sample_plan) == "confirm"
        assert _detect_plan_decision("执行", sample_plan) == "confirm"
        assert _detect_plan_decision("可以", sample_plan) == "confirm"
        assert _detect_plan_decision("ok", sample_plan) == "confirm"
        assert _detect_plan_decision("OK", sample_plan) == "confirm"
        assert _detect_plan_decision("yes", sample_plan) == "confirm"

    def test_exact_confirm_with_trailing_punctuation(self, sample_plan):
        assert _detect_plan_decision("确认。", sample_plan) == "confirm"
        assert _detect_plan_decision("好的！", sample_plan) == "confirm"
        assert _detect_plan_decision("  可以~ ", sample_plan) == "confirm"

    def test_exact_reject_words(self, sample_plan):
        assert _detect_plan_decision("取消", sample_plan) == "reject"
        assert _detect_plan_decision("算了", sample_plan) == "reject"
        assert _detect_plan_decision("no", sample_plan) == "reject"
        assert _detect_plan_decision("cancel", sample_plan) == "reject"
        # 曾因子串匹配 "行" 被误判为 confirm 的历史 bug
        assert _detect_plan_decision("不行", sample_plan) == "reject"

    def test_longer_messages_are_neutral(self, sample_plan):
        assert _detect_plan_decision("今天天气不错", sample_plan) == ""
        assert _detect_plan_decision("我觉得角色设定可以换个方向", sample_plan) == ""
        assert _detect_plan_decision("", sample_plan) == ""

    def test_no_plan_returns_empty(self):
        assert _detect_plan_decision("确认", {}) == ""
        assert _detect_plan_decision("确认", {"steps": []}) == ""


class TestConfirmBreakPath:
    """Confirming a pending plan must not crash the final-generation phase."""

    async def test_confirm_executes_plan_and_generates_final_reply(self):
        """pending plan + 用户消息"确认" → 走完整确认路径，不抛 NameError。

        The confirm branch ``break``s out of the main while loop before the
        per-turn assignments of ``active_model`` / ``assistant_text_parts``;
        the final generation stage must still work.
        """
        llm = MagicMock()

        async def fake_stream_tokens(**kwargs):
            yield "计划已执行"
            yield "完成"

        llm.chat_stream_tokens = fake_stream_tokens
        db = AsyncMock()

        initial = {
            # 空 project_id → 跳过 Phase 0 上下文加载
            "project_id": "",
            "user_id": "u1",
            "mode": "chat",
            "messages": [Message(role="user", content="确认")],
            "pending_plan": {
                "steps": [
                    {
                        "tool": "create_character",
                        "params": {"name": "测试角色"},
                        "tool_use_id": "tc_1",
                    }
                ]
            },
        }

        events = []
        with patch(
            "app.agent.response_builder.build_system_prompt",
            new=AsyncMock(return_value="SYS"),
        ):
            async for ev in autonomous_loop(initial, {}, llm, db, ""):
                events.append(ev)

        # Loop completed normally
        assert any(e.get("_loop_done") for e in events)

        # Plan step was executed (tool not registered → failure result, but
        # the confirm path itself must run it)
        tool_events = [e for e in events if "_tool_done" in e]
        assert len(tool_events) == 1
        assert tool_events[0]["_tool_done"]["tool"] == "create_character"

        # Final reply streamed without the NameError fallback message
        tokens = "".join(e["_stream_token"] for e in events if "_stream_token" in e)
        assert "计划已执行完成" in tokens
        assert "生成回复时出错" not in tokens

        # Plan was consumed
        final = [e for e in events if e.get("_loop_done")][0]["final_state"]
        assert final["pending_plan"] == {}
        assert final["plan_confirmed"] is True


class TestConfirmResumesLoop:
    """Confirming a plan must NOT stop the loop: it resumes the SAME iteration,
    feeds the plan's tool results back to the LLM, and keeps executing the
    chained multi-step task (fix for the '确认后执行完工具就停止' bug)."""

    async def test_confirm_feeds_tool_results_back_to_llm(self):
        """确认后 loop 继续：LLM 再次被调用，且能看到 plan 工具的执行结果。"""
        llm = MagicMock()
        seen_calls: list[list[Message]] = []
        llm_call_count = 0

        async def fake_stream_with_tools(**kwargs):
            nonlocal llm_call_count
            llm_call_count += 1
            seen_calls.append(kwargs["messages"])
            yield {"content": "", "tool_call": None}

        llm.chat_stream_with_tools = fake_stream_with_tools

        async def fake_stream_tokens(**kwargs):
            yield "确认后继续处理完成"

        llm.chat_stream_tokens = fake_stream_tokens
        db = AsyncMock()

        initial = {
            "project_id": "",
            "user_id": "u1",
            "mode": "chat",
            "messages": [Message(role="user", content="确认")],
            "pending_plan": {
                "steps": [
                    {
                        "tool": "create_character",
                        "params": {"name": "测试角色"},
                        "tool_use_id": "tc_1",
                    }
                ]
            },
        }

        events = []
        with patch(
            "app.agent.response_builder.build_system_prompt",
            new=AsyncMock(return_value="SYS"),
        ):
            async for ev in autonomous_loop(initial, {}, llm, db, ""):
                events.append(ev)

        assert any(e.get("_loop_done") for e in events)

        # Plan step was executed
        tool_events = [e for e in events if "_tool_done" in e]
        assert len(tool_events) == 1
        assert tool_events[0]["_tool_done"]["tool"] == "create_character"

        # The loop CONTINUED: LLM was called again AFTER the plan execution,
        # and its messages include the tool result from the confirmed plan.
        assert llm_call_count >= 2, (
            f"expected the loop to resume (>=2 LLM calls), got {llm_call_count}"
        )

        # The post-confirm LLM call sees a role=tool message for tc_1
        resumed_msgs = seen_calls[1]
        roles_after_confirm = [m.role for m in resumed_msgs]
        assert "tool" in roles_after_confirm
        tool_msg = [m for m in resumed_msgs if m.role == "tool"][0]
        assert tool_msg.tool_call_id == "tc_1"

        final = [e for e in events if e.get("_loop_done")][0]["final_state"]
        assert final["pending_plan"] == {}
        assert final["plan_confirmed"] is True


class TestEmptyAssistantStripped:
    """A restored plan snapshot may leave an empty assistant message
    (content=None, tool_calls stripped).  The DeepSeek/OpenAI API rejects
    assistant messages without content or tool_calls, so the messages sent
    to the LLM after a plan confirmation must never contain one."""

    def _initial_with_empty_assistant(self) -> dict:
        # Reproduces the real bug shape: turn 1 the LLM emitted only
        # tool_calls (content=None) for a destructive tool; the snapshot
        # restore strips the tool_calls and keeps an empty assistant shell;
        # the user then confirms.
        return {
            "project_id": "",
            "user_id": "u1",
            "mode": "chat",
            "messages": [
                Message(role="user", content="把那个角色删掉"),
                Message(role="assistant", content=None),  # 空壳 — 必须被剔除
                Message(role="user", content="确认"),
            ],
            "pending_plan": {
                "steps": [
                    {
                        "tool": "delete_character",
                        "params": {"character_id": "00000000-0000-0000-0000-000000000001"},
                        "tool_use_id": "tc_empty",
                    }
                ]
            },
        }

    async def test_no_empty_assistant_message_reaches_llm(self):
        llm = MagicMock()
        seen_calls: list[list[Message]] = []

        async def fake_stream_with_tools(**kwargs):
            seen_calls.append(kwargs["messages"])
            yield {"content": "", "tool_call": None}

        llm.chat_stream_with_tools = fake_stream_with_tools

        async def fake_stream_tokens(**kwargs):
            yield "删除完成"

        llm.chat_stream_tokens = fake_stream_tokens
        db = AsyncMock()

        events = []
        with patch(
            "app.agent.response_builder.build_system_prompt",
            new=AsyncMock(return_value="SYS"),
        ):
            async for ev in autonomous_loop(self._initial_with_empty_assistant(), {}, llm, db, ""):
                events.append(ev)

        assert any(e.get("_loop_done") for e in events)

        # Every LLM call must be free of content-less, tool_call-less
        # assistant messages — otherwise the API returns 400
        # "Invalid assistant message: content or tool_calls must be set".
        for msgs in seen_calls:
            bad = [
                m for m in msgs
                if m.role == "assistant" and not m.content and not m.tool_calls
            ]
            assert not bad, f"empty assistant message reached the LLM: {bad}"

        # The confirm path still executed the plan step
        tool_events = [e for e in events if "_tool_done" in e]
        assert len(tool_events) == 1
        assert tool_events[0]["_tool_done"]["tool"] == "delete_character"

