"""Tests for plan confirm/reject detection logic (now in loop.py)."""

import pytest
from app.agent.loop import _detect_plan_decision


class TestDetectPlanDecision:
    """Tests for _detect_plan_decision() which returns "confirm", "reject", or ""."""

    @pytest.fixture
    def sample_plan(self):
        return {"steps": [{"tool": "create_character", "description": "创建角色"}]}

    def test_basic_confirm(self, sample_plan):
        assert _detect_plan_decision("确认", sample_plan) == "confirm"
        assert _detect_plan_decision("执行", sample_plan) == "confirm"
        assert _detect_plan_decision("yes", sample_plan) == "confirm"
        assert _detect_plan_decision("ok", sample_plan) == "confirm"
        assert _detect_plan_decision("同意", sample_plan) == "confirm"
        assert _detect_plan_decision("就这样", sample_plan) == "confirm"
        assert _detect_plan_decision("按这个来", sample_plan) == "confirm"

    def test_basic_reject(self, sample_plan):
        assert _detect_plan_decision("取消", sample_plan) == "reject"
        assert _detect_plan_decision("算了", sample_plan) == "reject"
        assert _detect_plan_decision("no", sample_plan) == "reject"
        assert _detect_plan_decision("换个", sample_plan) == "reject"
        assert _detect_plan_decision("重新", sample_plan) == "reject"

    def test_neutral_message(self, sample_plan):
        assert _detect_plan_decision("今天天气不错", sample_plan) == ""
        assert _detect_plan_decision("我写了一个故事", sample_plan) == ""
        assert _detect_plan_decision("", sample_plan) == ""

    def test_no_plan_returns_empty(self):
        """Without a pending plan, no decision should be made."""
        assert _detect_plan_decision("确认", {}) == ""
        assert _detect_plan_decision("执行", {"steps": []}) == ""

    def test_reject_keywords_in_feedback(self, sample_plan):
        """Reject keywords in longer feedback should NOT match unless standalone."""
        # Long message containing "换个" but in context — should be empty
        assert _detect_plan_decision("我觉得角色设定可以换个方向", sample_plan) == ""

    def test_short_reject(self, sample_plan):
        """Short messages with reject keywords should match.

        With exact whole-message matching, "不行" is now a reject
        keyword — the old substring bug ("不行" contains confirm
        keyword "行") is fixed.
        """
        assert _detect_plan_decision("算了", sample_plan) == "reject"
        assert _detect_plan_decision("取消", sample_plan) == "reject"
        assert _detect_plan_decision("不行", sample_plan) == "reject"
