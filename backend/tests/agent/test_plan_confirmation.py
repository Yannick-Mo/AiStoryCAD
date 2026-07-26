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
