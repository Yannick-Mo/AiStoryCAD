"""Tests for analysis v2 tools: AnalyzeChapterTool, AnalyzeCharacterArcTool, SuggestNextTool, ProjectHealthTool."""
import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.agent.tools.analysis_v2_tools import (
    AnalyzeChapterTool,
    AnalyzeCharacterArcTool,
    SuggestNextTool,
    ProjectHealthTool,
    _safe_get,
)
from app.agent.tools.base import ToolResult


class TestSafeGet:
    def test_dict_access(self):
        assert _safe_get({"a": {"b": 1}}, "a", "b") == 1

    def test_missing_key_returns_default(self):
        assert _safe_get({"a": 1}, "b", default=None) is None

    def test_list_access(self):
        assert _safe_get({"a": [10, 20]}, "a", 1) == 20

    def test_list_index_error(self):
        assert _safe_get({"a": []}, "a", 5, default="x") == "x"

    def test_none_intermediate(self):
        assert _safe_get({"a": None}, "a", "b", default="x") == "x"

    def test_non_dict_or_list(self):
        assert _safe_get(42, "a", default="x") == "x"


@pytest.fixture(autouse=True)
def _stop_global_patches():
    yield
    patch.stopall()


def _mock_builder(method: str, return_value):
    mock_ctx_builder = patch("app.agent.tools.analysis_v2_tools.ContextBuilder").start()
    instance = MagicMock()
    setattr(instance, method, AsyncMock(return_value=return_value))
    mock_ctx_builder.return_value = instance
    return mock_ctx_builder


def _chapter_focus_fixture():
    return {
        "chapter": {"id": str(uuid.uuid4()), "title": "第一章", "goal": "引入主角", "status": "draft", "sort_order": 1},
        "act": {"id": str(uuid.uuid4()), "name": "第一幕"},
        "prev_chapter": {"title": "序章", "goal": "开场", "sort_order": 0},
        "next_chapter": None,
        "scenes": [
            {
                "id": str(uuid.uuid4()), "title": "开场", "pov_character": "张三",
                "setting": "森林", "scene_time": "白天", "summary": "主角登场",
                "sort_order": 1, "content": "清晨的森林里，张三踏上了旅途。", "content_cut": False,
            },
        ],
        "scene_count": 1,
        "body_chars": 25,
        "content_truncated": False,
    }


class TestAnalyzeChapterTool:
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_chapter_success(self, mock_get_shared, mock_verify):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps({
                "scores": {"structure": 8, "pacing": 7, "character": 9, "language": 6},
                "analysis": "章节结构完整，节奏有提升空间",
                "suggestions": ["加强动作描写", "调整场景长度"],
            })
        )
        mock_get_shared.return_value.fork.return_value = mock_llm
        _mock_builder("build_chapter_focus", _chapter_focus_fixture())

        tool = AnalyzeChapterTool()
        result = await tool.run(db=AsyncMock(), project_id=str(uuid.uuid4()), chapter_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["scores"]["structure"] == 8
        assert "节奏" in result.data["analysis"]

    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_chapter_not_found(self, mock_get_shared, mock_verify):
        _mock_builder("build_chapter_focus", None)

        tool = AnalyzeChapterTool()
        result = await tool.run(db=AsyncMock(), project_id=str(uuid.uuid4()), chapter_id=str(uuid.uuid4()))

        assert result.success is False
        assert result.error == "Chapter not found"

    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    async def test_analyze_chapter_invalid_uuid(self, mock_verify):
        db = AsyncMock()
        tool = AnalyzeChapterTool()

        result = await tool.run(db=db, project_id="not-a-uuid", chapter_id=str(uuid.uuid4()))

        assert result.success is False

    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_chapter_json_decode_error(self, mock_get_shared, mock_verify):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="not json at all")
        mock_get_shared.return_value.fork.return_value = mock_llm
        _mock_builder("build_chapter_focus", _chapter_focus_fixture())

        tool = AnalyzeChapterTool()
        result = await tool.run(db=AsyncMock(), project_id=str(uuid.uuid4()), chapter_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["scores"] == {}
        assert result.data["analysis"] == "not json at all"

    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_chapter_notes_truncation(self, mock_get_shared, mock_verify):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content=json.dumps({"analysis": "ok"}))
        mock_get_shared.return_value.fork.return_value = mock_llm
        focus = _chapter_focus_fixture()
        focus["content_truncated"] = True
        _mock_builder("build_chapter_focus", focus)

        result = await AnalyzeChapterTool().run(
            db=AsyncMock(), project_id=str(uuid.uuid4()), chapter_id=str(uuid.uuid4()))

        assert result.success is True
        assert "_note" in result.data


def _character_focus_fixture(scenes=()):
    return {
        "character": {"id": str(uuid.uuid4()), "name": "张三", "role": "主角",
                      "personality": "勇敢", "appearance": "", "background": "山村少年", "motivation": "复仇"},
        "appearances": list(scenes),
        "appearance_count": len(list(scenes)),
        "relations": [],
    }


class TestAnalyzeCharacterArcTool:
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_character_arc_success(self, mock_get_shared, mock_verify):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps({
                "arc_type": "redemption",
                "consistency_score": 8,
                "analysis": "角色弧线清晰",
                "issues": ["第三幕动机转变略显突兀"],
                "suggestions": ["增加一个关键事件"],
            })
        )
        mock_get_shared.return_value.fork.return_value = mock_llm
        _mock_builder("build_character_focus", _character_focus_fixture(scenes=[
            {"scene_id": str(uuid.uuid4()), "chapter_title": "第一章", "chapter_order": 1,
             "act_order": 1, "scene_title": "森林之旅", "scene_order": 1, "body_preview": "张三在森林中探险", "body_len": 10},
        ]))

        result = await AnalyzeCharacterArcTool().run(
            db=AsyncMock(), project_id=str(uuid.uuid4()), character_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["arc_type"] == "redemption"
        assert result.data["consistency_score"] == 8

    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_character_not_found(self, mock_get_shared, mock_verify):
        _mock_builder("build_character_focus", None)

        result = await AnalyzeCharacterArcTool().run(
            db=AsyncMock(), project_id=str(uuid.uuid4()), character_id=str(uuid.uuid4()))

        assert result.success is False
        assert result.error == "Character not found"

    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_character_no_scenes(self, mock_get_shared, mock_verify):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps({"arc_type": "flat", "consistency_score": 5,
                               "analysis": "角色出现较少", "issues": [], "suggestions": []})
        )
        mock_get_shared.return_value.fork.return_value = mock_llm
        _mock_builder("build_character_focus", _character_focus_fixture())

        result = await AnalyzeCharacterArcTool().run(
            db=AsyncMock(), project_id=str(uuid.uuid4()), character_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["arc_type"] == "flat"


class TestSuggestNextTool:
    def _make_mock_llm(self, response_data: dict):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content=json.dumps(response_data))
        return mock_llm

    def _progress(self, **over):
        base = {
            "total_acts": 1, "total_chapters": 1, "total_scenes": 2,
            "written_scenes": 1, "progress_pct": 50,
            "recent_written": [{"act": "第一幕", "chapter": "第一章", "scene": "开场", "scene_id": "s1"}],
            "unwritten_candidates": [{"act": "第一幕", "chapter": "第一章", "scene": "发展", "scene_id": "s2"}],
        }
        base.update(over)
        return base

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_suggest_next_success(self, mock_get_shared, mock_verify, mock_ctx_builder):
        mock_ctx_builder.return_value = MagicMock()
        mock_ctx_builder.return_value.build_writing_progress = AsyncMock(return_value=self._progress())
        mock_get_shared.return_value.fork.return_value = self._make_mock_llm({
            "focus": "场景'发展'",
            "reason": "这是第二场，需要推动情节",
            "suggested_scene": "第一幕→第一章→场景'发展'",
            "tips": ["从冲突开始写"],
        })

        result = await SuggestNextTool().run(db=AsyncMock(), project_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["total_scenes"] == 2
        assert result.data["written_scenes"] == 1
        assert result.data["progress_pct"] == 50
        assert result.data["focus"] == "场景'发展'"

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_suggest_next_no_unwritten(self, mock_get_shared, mock_verify, mock_ctx_builder):
        mock_ctx_builder.return_value = MagicMock()
        mock_ctx_builder.return_value.build_writing_progress = AsyncMock(return_value=self._progress(
            total_scenes=1, written_scenes=1, progress_pct=100, unwritten_candidates=[]))
        mock_get_shared.return_value.fork.return_value = self._make_mock_llm({
            "focus": "all done", "reason": "全部完成",
            "suggested_scene": "", "tips": ["开始下一章"]
        })

        result = await SuggestNextTool().run(db=AsyncMock(), project_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["progress_pct"] == 100

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_suggest_next_no_acts(self, mock_get_shared, mock_verify, mock_ctx_builder):
        mock_ctx_builder.return_value = MagicMock()
        mock_ctx_builder.return_value.build_writing_progress = AsyncMock(return_value=self._progress(
            total_acts=0, total_chapters=0, total_scenes=0, written_scenes=0,
            progress_pct=0, recent_written=[], unwritten_candidates=[]))
        mock_get_shared.return_value.fork.return_value = self._make_mock_llm({})

        result = await SuggestNextTool().run(db=AsyncMock(), project_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["total_acts"] == 0
        assert result.data["progress_pct"] == 0


class TestProjectHealthTool:
    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    async def test_project_health_all_healthy(self, mock_verify, mock_ctx_builder):
        mock_ctx_builder.return_value = MagicMock()
        mock_ctx_builder.return_value.build_health_snapshot = AsyncMock(return_value={
            "total_acts": 1, "total_chapters": 1, "total_scenes": 1,
            "written_scenes": 1, "unwritten_scenes_count": 0, "unwritten_scenes": [],
            "empty_chapters_count": 0, "empty_chapters": [],
            "total_characters": 1, "isolated_characters_count": 0, "isolated_characters": [],
            "total_edges": 1,
        })

        result = await ProjectHealthTool().run(db=AsyncMock(), project_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["total_chapters"] == 1
        assert result.data["unwritten_scenes_count"] == 0
        assert result.data["empty_chapters_count"] == 0
        assert result.data["isolated_characters_count"] == 0

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    async def test_project_health_issues_detected(self, mock_verify, mock_ctx_builder):
        mock_ctx_builder.return_value = MagicMock()
        mock_ctx_builder.return_value.build_health_snapshot = AsyncMock(return_value={
            "total_acts": 1, "total_chapters": 2, "total_scenes": 2,
            "written_scenes": 1, "unwritten_scenes_count": 1,
            "unwritten_scenes": [{"act": "第一幕", "chapter": "未写完", "scene": "未写场景"}],
            "empty_chapters_count": 1,
            "empty_chapters": [{"act": "第一幕", "chapter": "空章"}],
            "total_characters": 2, "isolated_characters_count": 1,
            "isolated_characters": ["孤立角色"],
            "total_edges": 0,
        })

        result = await ProjectHealthTool().run(db=AsyncMock(), project_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["empty_chapters_count"] == 1
        assert result.data["unwritten_scenes_count"] == 1
        assert result.data["isolated_characters_count"] == 1
        assert "孤立角色" in result.data["isolated_characters"]

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    async def test_project_health_empty_project(self, mock_verify, mock_ctx_builder):
        mock_ctx_builder.return_value = MagicMock()
        mock_ctx_builder.return_value.build_health_snapshot = AsyncMock(return_value={
            "total_acts": 0, "total_chapters": 0, "total_scenes": 0,
            "written_scenes": 0, "unwritten_scenes_count": 0, "unwritten_scenes": [],
            "empty_chapters_count": 0, "empty_chapters": [],
            "total_characters": 0, "isolated_characters_count": 0, "isolated_characters": [],
            "total_edges": 0,
        })

        result = await ProjectHealthTool().run(db=AsyncMock(), project_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["total_acts"] == 0
        assert result.data["total_characters"] == 0
        assert result.data["total_edges"] == 0
