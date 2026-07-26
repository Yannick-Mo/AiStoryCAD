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
        d = {"a": {"b": {"c": 42}}}
        assert _safe_get(d, "a", "b", "c") == 42

    def test_missing_key_returns_default(self):
        assert _safe_get({"a": 1}, "b", default="fallback") == "fallback"

    def test_list_access(self):
        assert _safe_get({"items": [10, 20, 30]}, "items", 1) == 20

    def test_list_index_error(self):
        assert _safe_get({"items": [10]}, "items", 5, default=0) == 0

    def test_none_intermediate(self):
        assert _safe_get({"a": None}, "a", "b", default=0) == 0

    def test_non_dict_or_list(self):
        assert _safe_get(42, "a", "b", default=None) is None


class TestAnalyzeChapterTool:
    def _mock_db_for_chapter(self):
        db = AsyncMock()
        ch_row = MagicMock()
        ch_row.scalar_one_or_none.return_value = MagicMock(
            id=uuid.uuid4(), title="第一章", goal="引入主角", status="draft", act_id=None
        )
        scene_mock = MagicMock(
            id=uuid.uuid4(), title="场景1", pov_character="张三", setting="森林",
            summary="主角进入森林", sort_order=1
        )
        scenes_row = MagicMock()
        scenes_row.scalars.return_value.all.return_value = [scene_mock]
        content_row = MagicMock()
        content_row.scalar_one_or_none.return_value = MagicMock(
            content="在一片茂密的森林中..."
        )
        db.execute = AsyncMock(side_effect=[ch_row, scenes_row, content_row])
        return db

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    async def test_analyze_chapter_success(self, mock_verify, mock_get_shared, mock_ctx_builder):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(
            content=json.dumps({
                "scores": {"structure": 8, "pacing": 7, "character": 9, "language": 6},
                "analysis": "章节结构完整，节奏有提升空间",
                "suggestions": ["加强动作描写", "调整场景长度"],
            })
        )
        mock_get_shared.return_value.fork.return_value = mock_llm

        mock_ctx_instance = MagicMock()
        mock_ctx_instance.build_full = AsyncMock(return_value={"mock": "context"})
        mock_ctx_builder.return_value = mock_ctx_instance

        tool = AnalyzeChapterTool()
        result = await tool.run(db=self._mock_db_for_chapter(), project_id=str(uuid.uuid4()), chapter_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["scores"]["structure"] == 8
        assert "节奏" in result.data["analysis"]

    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_chapter_not_found(self, mock_get_shared, mock_verify):
        db = AsyncMock()
        ch_row = MagicMock()
        ch_row.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=ch_row)

        tool = AnalyzeChapterTool()
        result = await tool.run(db=db, project_id=str(uuid.uuid4()), chapter_id=str(uuid.uuid4()))

        assert result.success is False
        assert result.error == "Chapter not found"

    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    async def test_analyze_chapter_invalid_uuid(self, mock_verify):
        db = AsyncMock()
        tool = AnalyzeChapterTool()

        result = await tool.run(db=db, project_id="not-a-uuid", chapter_id=str(uuid.uuid4()))

        assert result.success is False

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_chapter_json_decode_error(self, mock_get_shared, mock_verify, mock_ctx_builder):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="not json at all")
        mock_get_shared.return_value.fork.return_value = mock_llm

        mock_ctx_instance = MagicMock()
        mock_ctx_instance.build_full = AsyncMock(return_value={})
        mock_ctx_builder.return_value = mock_ctx_instance

        db = AsyncMock()
        ch_row = MagicMock()
        ch_row.scalar_one_or_none.return_value = MagicMock(
            id=uuid.uuid4(), title="Test", goal="Test goal", status="draft", act_id=None
        )
        scenes_row = MagicMock()
        scenes_row.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[ch_row, scenes_row])

        tool = AnalyzeChapterTool()
        result = await tool.run(db=db, project_id=str(uuid.uuid4()), chapter_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["scores"] == {}
        assert result.data["analysis"] == "not json at all"


class TestAnalyzeCharacterArcTool:
    def _make_db_for_character(self):
        db = AsyncMock()
        char_row = MagicMock()
        char_row.scalar_one_or_none.return_value = MagicMock(
            id=uuid.uuid4(), name="张三", role="主角", description="勇敢的少年"
        )
        scene_mock = MagicMock(
            id=uuid.uuid4(), title="森林之旅", pov_character="张三", sort_order=1
        )
        scenes_row = MagicMock()
        scenes_row.scalars.return_value.all.return_value = [scene_mock]
        content_row = MagicMock()
        content_row.scalar_one_or_none.return_value = MagicMock(content="张三在森林中探险")
        rels_row = MagicMock()
        rels_row.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[char_row, scenes_row, content_row, rels_row])
        return db

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

        result = await AnalyzeCharacterArcTool().run(
            db=self._make_db_for_character(), project_id=str(uuid.uuid4()),
            character_id=str(uuid.uuid4())
        )

        assert result.success is True
        assert result.data["arc_type"] == "redemption"
        assert result.data["consistency_score"] == 8

    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_analyze_character_not_found(self, mock_get_shared, mock_verify):
        db = AsyncMock()
        char_row = MagicMock()
        char_row.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=char_row)

        result = await AnalyzeCharacterArcTool().run(
            db=db, project_id=str(uuid.uuid4()), character_id=str(uuid.uuid4())
        )

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

        db = AsyncMock()
        char_row = MagicMock()
        char_row.scalar_one_or_none.return_value = MagicMock(
            id=uuid.uuid4(), name="配角", role="配角", description="背景角色"
        )
        scenes_row = MagicMock()
        scenes_row.scalars.return_value.all.return_value = []
        rels_row = MagicMock()
        rels_row.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[char_row, scenes_row, rels_row])

        result = await AnalyzeCharacterArcTool().run(
            db=db, project_id=str(uuid.uuid4()), character_id=str(uuid.uuid4())
        )

        assert result.success is True
        assert result.data["arc_type"] == "flat"


class TestSuggestNextTool:
    def _make_mock_llm(self, response_data: dict):
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content=json.dumps(response_data))
        return mock_llm

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    @patch("app.agent.tools.analysis_v2_tools.get_shared_client")
    async def test_suggest_next_success(self, mock_get_shared, mock_verify, mock_ctx_builder):
        mock_ctx_builder.return_value = MagicMock()
        mock_ctx_builder.return_value.build_full = AsyncMock(return_value={
            "acts": [
                {
                    "name": "第一幕",
                    "chapters": [
                        {
                            "title": "第一章",
                            "scenes": [
                                {"title": "开场", "content_preview": "正文内容"},
                                {"title": "发展", "content_preview": None},
                            ],
                        }
                    ],
                }
            ]
        })
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
        mock_ctx_builder.return_value.build_full = AsyncMock(return_value={
            "acts": [{"name": "第一幕", "chapters": [{"title": "第一章", "scenes": [{"title": "开场", "content_preview": "正文"}]}]}]
        })
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
        mock_ctx_builder.return_value.build_full = AsyncMock(return_value={})
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
        mock_ctx_builder.return_value.build_full = AsyncMock(return_value={
            "acts": [{"name": "第一幕", "chapters": [{"title": "第一章", "scenes": [{"title": "开场", "content_preview": "正文内容"}]}]}],
            "characters": [{"id": "c1", "name": "张三"}],
            "relations": [{"character_id": "c1", "target_id": "c2"}],
            "edges": ["e1"],
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
        mock_ctx_builder.return_value.build_full = AsyncMock(return_value={
            "acts": [
                {"name": "第一幕", "chapters": [
                    {"title": "空章", "scenes": []},
                    {"title": "未写完", "scenes": [{"title": "未写场景", "content_preview": None}]},
                ]}
            ],
            "characters": [{"id": "c1", "name": "有关系的角色"}, {"id": "c2", "name": "孤立角色"}],
            "relations": [{"character_id": "c1", "target_id": "c3"}],
            "edges": [],
        })

        result = await ProjectHealthTool().run(db=AsyncMock(), project_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["empty_chapters_count"] == 1
        assert result.data["unwritten_scenes_count"] == 1
        assert result.data["isolated_characters_count"] == 1
        assert "孤立角色" in result.data["isolated_characters"]

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    async def test_project_health_from_list_context(self, mock_verify, mock_ctx_builder):
        mock_ctx_builder.return_value = MagicMock()
        mock_ctx_builder.return_value.build_full = AsyncMock(return_value=[
            {"name": "first_act", "chapters": []},
        ])

        result = await ProjectHealthTool().run(db=AsyncMock(), project_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["total_acts"] == 0

    @patch("app.agent.tools.analysis_v2_tools.ContextBuilder")
    @patch("app.agent.tools.analysis_v2_tools.verify_project_owner")
    async def test_project_health_missing_keys(self, mock_verify, mock_ctx_builder):
        mock_ctx_builder.return_value = MagicMock()
        mock_ctx_builder.return_value.build_full = AsyncMock(return_value={})

        result = await ProjectHealthTool().run(db=AsyncMock(), project_id=str(uuid.uuid4()))

        assert result.success is True
        assert result.data["total_acts"] == 0
        assert result.data["total_characters"] == 0
        assert result.data["total_edges"] == 0