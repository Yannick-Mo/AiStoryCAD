"""Integration tests for autonomous agent loop and state management.

Tests verify state transitions, confirm/reject detection, and token
streaming without making real LLM API calls.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.loop_state import LoopState
from app.agent.loop import _detect_plan_decision


class TestLoopState:
    """Verify LoopState immutability and serialization."""

    def test_from_initial_basic(self):
        state = LoopState.from_initial({
            "project_id": "proj-1",
            "user_id": "user-1",
            "mode": "chat",
        })
        assert state.project_id == "proj-1"
        assert state.user_id == "user-1"
        assert state.mode == "chat"

    def test_from_initial_empty(self):
        state = LoopState.from_initial({})
        assert state.project_id == ""
        assert state.mode == "chat"
        assert state.turn_count == 0

    def test_replace_immutable(self):
        state = LoopState(project_id="p1", mode="chat")
        new_state = state.replace(mode="cowriter")
        assert state.mode == "chat"  # original unchanged
        assert new_state.mode == "cowriter"
        assert new_state.project_id == "p1"  # preserved

    def test_replace_multiple(self):
        state = LoopState()
        new_state = state.replace(
            project_id="p2",
            turn_count=5,
            _context_loaded=True,
        )
        assert new_state.project_id == "p2"
        assert new_state.turn_count == 5
        assert new_state._context_loaded is True

    def test_to_dict_roundtrip(self):
        original = LoopState(
            project_id="p3",
            user_id="u1",
            mode="cowriter",
            turn_count=3,
            active_skills=["skill1"],
            errors=["err1"],
        )
        d = original.to_dict()
        assert d["project_id"] == "p3"
        assert d["mode"] == "cowriter"
        assert d["active_skills"] == ["skill1"]
        assert d["errors"] == ["err1"]

    def test_from_initial_preserves_active_skills(self):
        state = LoopState.from_initial({"active_skills": ["skill_a", "skill_b"]})
        assert state.active_skills == ["skill_a", "skill_b"]

    def test_from_initial_none_fields_become_defaults(self):
        state = LoopState.from_initial({"project_id": None, "user_id": None})
        assert state.project_id == ""
        assert state.user_id == ""


class TestDetectPlanDecision:
    """Tests for _detect_plan_decision() — confirm/reject detection."""

    @pytest.fixture
    def sample_plan(self):
        return {"steps": [{"tool": "create_character", "description": "创建角色"}]}

    def test_basic_confirm(self, sample_plan):
        assert _detect_plan_decision("确认", sample_plan) == "confirm"
        assert _detect_plan_decision("执行", sample_plan) == "confirm"
        assert _detect_plan_decision("yes", sample_plan) == "confirm"

    def test_basic_reject(self, sample_plan):
        assert _detect_plan_decision("取消", sample_plan) == "reject"
        assert _detect_plan_decision("算了", sample_plan) == "reject"
        assert _detect_plan_decision("重新", sample_plan) == "reject"

    def test_neutral_message(self, sample_plan):
        assert _detect_plan_decision("今天天气不错", sample_plan) == ""
        assert _detect_plan_decision("", sample_plan) == ""

    def test_no_plan_returns_empty(self):
        assert _detect_plan_decision("确认", {}) == ""
        assert _detect_plan_decision("执行", {"steps": []}) == ""

    def test_short_reject(self, sample_plan):
        """Short messages with reject keywords should match.

        With exact whole-message matching, "不行" is a reject keyword —
        the old substring bug ("不行" contains "行") is fixed.
        """
        assert _detect_plan_decision("算了", sample_plan) == "reject"
        assert _detect_plan_decision("取消", sample_plan) == "reject"
        assert _detect_plan_decision("不行", sample_plan) == "reject"
