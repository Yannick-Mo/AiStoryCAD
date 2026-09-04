"""Tests for the range-read tools: ReadChaptersTool, ReadRecentTool and
their material builders (build_chapter_window / build_recent_items), plus
read_scene content pagination.

Covers the post-refactor read surface:
- new tools registered + read-only, params shape
- param validation (window bounds, kind enum, n clamp)
- budget trimming markers (truncated / next_from)
- recent-items assembly (edit-time order, chapter dedup, no body text)
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.context import ContextBuilder
from app.agent.tools.base import ToolMeta
from app.agent.tools.range_tools import ReadChaptersTool, ReadRecentTool


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _QueryResult:
    """Minimal stand-in for an AsyncSession.execute() result."""

    def __init__(self, rows=None, scalars=None):
        self._rows = rows if rows is not None else []
        self._scalars = scalars if scalars is not None else []

    def all(self):
        return self._rows

    def scalars(self):
        return _ScalarsResult(self._scalars)

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None


class _ScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


def _chapter(uid, act_id, title="章", goal="", status="draft", sort_order=1):
    return type("Ch", (), {
        "id": uid, "act_id": act_id, "project_id": uuid.uuid4(),
        "title": title, "goal": goal, "status": status, "sort_order": sort_order,
    })()


def _fake_db(*query_results):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(query_results))
    return db


def _scene(uid, chapter_id, title="场景", summary="蓝图", word_count=0, sort_order=1):
    return type("Sc", (), {
        "id": uid, "chapter_id": chapter_id, "project_id": uuid.uuid4(),
        "title": title, "summary": summary, "word_count": word_count,
        "sort_order": sort_order,
    })()


# ---------------------------------------------------------------------------
# registration / meta
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_read_chapters_meta(self):
        assert ReadChaptersTool.meta.name == "read_chapters"
        assert ReadChaptersTool.meta.concurrency.value == "safe"
        assert ReadChaptersTool.meta.max_result_chars == 12000
        props = ReadChaptersTool.meta.parameters["properties"]
        assert {"chapter_from", "chapter_to"} <= set(props)
        assert ReadChaptersTool.meta.parameters["required"] == ["chapter_from", "chapter_to"]
        assert "全局章序号" in ReadChaptersTool.meta.description

    def test_read_recent_meta(self):
        assert ReadRecentTool.meta.name == "read_recent"
        props = ReadRecentTool.meta.parameters["properties"]
        assert props["kind"]["enum"] == ["scenes", "chapters"]
        assert props["n"]["description"].find("1-10") >= 0

    def test_registry_and_modes(self):
        from app.agent.tools import get_tool_registry, get_filtered_tools
        reg = get_tool_registry()
        assert {"read_chapters", "read_recent"} <= set(reg)
        for name in ("read_chapters", "read_recent"):
            assert not reg[name].is_write_operation
        for mode in ("chat", "cowriter"):
            tools = get_filtered_tools(reg, mode=mode)
            assert "read_chapters" in tools and "read_recent" in tools, mode

    def test_deleted_tools_are_gone(self):
        from app.agent.tools import get_tool_registry
        from app.agent.tool_filter import verify_tool_registry
        reg = get_tool_registry()
        assert "read_project_overview" not in reg
        assert "list_chapters" not in reg
        assert "list_scenes" not in reg
        assert verify_tool_registry(reg) == []


# ---------------------------------------------------------------------------
# param validation (no DB needed)
# ---------------------------------------------------------------------------

class TestReadChaptersValidation:
    @patch("app.agent.tools.range_tools.verify_project_owner")
    @patch("app.agent.tools.range_tools.ContextBuilder")
    async def test_success_calls_window(self, mock_builder_cls, mock_verify):
        builder = MagicMock()
        builder.build_chapter_window = AsyncMock(return_value={"chapters": []})
        mock_builder_cls.return_value = builder
        tool = ReadChaptersTool()
        res = await tool.run(
            db=MagicMock(), project_id=str(uuid.uuid4()), user_id="u1",
            chapter_from=3, chapter_to=7,
        )
        assert res.success
        mock_verify.assert_awaited_once()
        builder.build_chapter_window.assert_awaited_once()
        args, kwargs = builder.build_chapter_window.call_args
        assert args[1] == 3 and args[2] == 7
        assert kwargs.get("include_goals") is True

    async def test_window_reversed_errors(self):
        tool = ReadChaptersTool()
        res = await tool.run(db=AsyncMock(), project_id=str(uuid.uuid4()),
                             user_id="u1", chapter_from=9, chapter_to=2)
        assert not res.success
        assert "起点大于终点" in res.error

    async def test_non_positive_errors(self):
        tool = ReadChaptersTool()
        res = await tool.run(db=AsyncMock(), project_id=str(uuid.uuid4()),
                             user_id="u1", chapter_from=0, chapter_to=5)
        assert not res.success


class TestReadRecentValidation:
    @patch("app.agent.tools.range_tools.verify_project_owner")
    @patch("app.agent.tools.range_tools.ContextBuilder")
    async def test_success_defaults(self, mock_builder_cls, mock_verify):
        builder = MagicMock()
        builder.build_recent_items = AsyncMock(return_value={"kind": "scenes", "items": []})
        mock_builder_cls.return_value = builder
        tool = ReadRecentTool()
        res = await tool.run(db=MagicMock(), project_id=str(uuid.uuid4()), user_id="u1")
        assert res.success
        builder.build_recent_items.assert_awaited_once()
        _, kwargs = builder.build_recent_items.call_args
        assert kwargs["kind"] == "scenes" and kwargs["n"] == 5

    @patch("app.agent.tools.range_tools.verify_project_owner")
    @patch("app.agent.tools.range_tools.ContextBuilder")
    async def test_n_clamped(self, mock_builder_cls, mock_verify):
        builder = MagicMock()
        builder.build_recent_items = AsyncMock(return_value={"kind": "chapters", "items": []})
        mock_builder_cls.return_value = builder
        tool = ReadRecentTool()
        res = await tool.run(db=MagicMock(), project_id=str(uuid.uuid4()),
                             user_id="u1", kind="chapters", n=99)
        assert res.success
        _, kwargs = builder.build_recent_items.call_args
        assert kwargs["n"] == 10

    async def test_bad_kind_errors(self):
        tool = ReadRecentTool()
        res = await tool.run(db=AsyncMock(), project_id=str(uuid.uuid4()),
                             user_id="u1", kind="scen")
        assert not res.success


# ---------------------------------------------------------------------------
# context builder: build_chapter_window
# ---------------------------------------------------------------------------

class TestBuildChapterWindow:
    def _window_db(self, chapters, acts, total_override=None):
        # 1) ordered chapter ids, 2) window chapter rows, 3) act id/name rows
        return _fake_db(
            _QueryResult(rows=[(c.id,) for c in chapters]),
            _QueryResult(scalars=chapters),
            _QueryResult(rows=[(a_id, name) for a_id, name in acts]),
        )

    async def test_basic_window_with_goals(self):
        a1, a2 = uuid.uuid4(), uuid.uuid4()
        chs = [
            _chapter(uuid.uuid4(), a1, "章一", goal="目标一", sort_order=1),
            _chapter(uuid.uuid4(), a2, "章二", goal="目标二", sort_order=1),
            _chapter(uuid.uuid4(), a2, "章三", goal="目标三", sort_order=2),
        ]
        db = self._window_db(chs, [(a1, "第一幕"), (a2, "第二幕")])
        builder = ContextBuilder(db)
        out = await builder.build_chapter_window(uuid.uuid4(), 2, 3)
        assert out["total_chapters"] == 3
        assert out["truncated"] is False
        assert [c["global_order"] for c in out["chapters"]] == [2, 3]
        assert out["chapters"][0]["title"] == "章二"
        assert out["chapters"][0]["goal"] == "目标二"
        assert out["chapters"][0]["act_name"] == "第二幕"
        assert out["chapters"][0]["act_id"] == str(a2)
        assert out["next_from"] is None

    async def test_window_past_end_trims(self):
        a1 = uuid.uuid4()
        chs = [_chapter(uuid.uuid4(), a1, "只有一章", goal="g")]
        db = self._window_db(chs, [(a1, "幕")])
        builder = ContextBuilder(db)
        out = await builder.build_chapter_window(uuid.uuid4(), 1, 99)
        assert out["chapters"][0]["global_order"] == 1
        assert out["total_chapters"] == 1

    async def test_window_outside_range_empty(self):
        a1 = uuid.uuid4()
        chs = [_chapter(uuid.uuid4(), a1, "只有一章", goal="g")]
        db = self._window_db(chs, [(a1, "幕")])
        builder = ContextBuilder(db)
        out = await builder.build_chapter_window(uuid.uuid4(), 5, 7)
        assert out["chapters"] == []
        assert out["total_chapters"] == 1

    async def test_include_goals_false_omits_goal(self):
        a1 = uuid.uuid4()
        chs = [_chapter(uuid.uuid4(), a1, "章", goal="目标全文")]
        db = self._window_db(chs, [(a1, "幕")])
        builder = ContextBuilder(db)
        out = await builder.build_chapter_window(uuid.uuid4(), 1, 1, include_goals=False)
        assert "goal" not in out["chapters"][0]

    async def test_budget_trims_with_next_from(self):
        a1 = uuid.uuid4()
        chs = [
            _chapter(uuid.uuid4(), a1, "章一", goal="短"),
            _chapter(uuid.uuid4(), a1, "章二", goal="目标" * 3000),  # ~6k chars
            _chapter(uuid.uuid4(), a1, "章三", goal="目标" * 3000),
        ]
        db = self._window_db(chs, [(a1, "幕")])
        builder = ContextBuilder(db)
        out = await builder.build_chapter_window(uuid.uuid4(), 1, 3, budget_chars=500)
        assert out["truncated"] is True
        assert out["next_from"] == 2
        assert len(out["chapters"]) == 1


# ---------------------------------------------------------------------------
# context builder: build_recent_items
# ---------------------------------------------------------------------------

class TestBuildRecentItems:
    def _recent_db(self, scenes, scene_rows, chapter_rows=None):
        # scenes: list[(Scene, chapter_title, act_name)] in detail order
        # scene_rows: list[(scene_id, updated_at)] most-recent-first
        qs = [
            _QueryResult(rows=scene_rows),
            _QueryResult(rows=[(sc, ch_t, act_n) for sc, ch_t, act_n in scenes]),
        ]
        if chapter_rows is not None:
            qs.append(_QueryResult(scalars=chapter_rows))
        return _fake_db(*qs)

    async def test_recent_scenes_shape(self):
        sid, cid = uuid.uuid4(), uuid.uuid4()
        sc = _scene(sid, cid, "最新一场", summary="蓝图预览内容" * 200, word_count=1234)
        now = datetime(2026, 9, 4, tzinfo=timezone.utc)
        db = self._recent_db([(sc, "第三章", "第二幕")], [(sid, now)])
        builder = ContextBuilder(db)
        out = await builder.build_recent_items(uuid.uuid4(), kind="scenes", n=5)
        assert out["truncated"] is False
        item = out["items"][0]
        assert item["scene_id"] == str(sid)
        assert item["chapter_title"] == "第三章"
        assert item["act_name"] == "第二幕"
        assert item["word_count"] == 1234
        assert len(item["summary_preview"]) <= 500
        assert "updated_at" in item
        assert "content" not in item

    async def test_recent_empty(self):
        db = _fake_db(_QueryResult(rows=[]))
        builder = ContextBuilder(db)
        out = await builder.build_recent_items(uuid.uuid4(), kind="scenes")
        assert out == {"kind": "scenes", "items": [], "truncated": False}

    async def test_recent_chapters_dedup_with_goal(self):
        cid1, cid2 = uuid.uuid4(), uuid.uuid4()
        s1 = _scene(uuid.uuid4(), cid1, "旧场", summary="s")
        s2 = _scene(uuid.uuid4(), cid2, "中场", summary="s")
        s3 = _scene(uuid.uuid4(), cid1, "新场", summary="s")
        ch1 = _chapter(cid1, uuid.uuid4(), "章甲", goal="目标甲")
        ch2 = _chapter(cid2, uuid.uuid4(), "章乙", goal="目标乙")
        now = datetime(2026, 9, 4, tzinfo=timezone.utc)
        # recent_rows are most-recent-first: s3 (newest) .. s1 (oldest)
        db = self._recent_db(
            [(s3, "章甲", "幕"), (s2, "章乙", "幕"), (s1, "章甲", "幕")],
            [(s3.id, now), (s2.id, now), (s1.id, now)],
            chapter_rows=[ch1, ch2],
        )
        builder = ContextBuilder(db)
        out = await builder.build_recent_items(uuid.uuid4(), kind="chapters", n=2)
        assert [i["chapter_id"] for i in out["items"]] == [str(cid1), str(cid2)]
        first = out["items"][0]
        assert first["goal"] == "目标甲"
        assert first["latest_scene_title"] == "新场"


# ---------------------------------------------------------------------------
# read_scene pagination (run-level with real models)
# ---------------------------------------------------------------------------

class TestReadScenePagination:
    async def _run(self, body, offset, limit):
        from types import SimpleNamespace

        from app.agent.tools.project_tools import ReadSceneTool

        pid, sid = uuid.uuid4(), uuid.uuid4()
        scene = SimpleNamespace(id=sid, project_id=pid, title="场")
        project = SimpleNamespace(id=pid)
        content = SimpleNamespace(content=body)
        tool = ReadSceneTool()
        db = _fake_db(
            _QueryResult(scalars=[scene]),
            _QueryResult(scalars=[project]),
            _QueryResult(scalars=[content]),
        )
        with patch("app.agent.tools.project_tools.row_to_dict",
                   return_value={"id": str(sid), "project_id": str(pid), "title": "场"}):
            return await tool.run(db, project_id=str(pid), user_id="u1",
                                  scene_id=str(sid), include_content=True,
                                  content_offset=offset, content_limit=limit)

    async def test_first_page_no_more(self):
        res = await self._run("短正文", 0, 6000)
        assert res.success
        assert res.data["content"] == "短正文"
        assert res.data["content_offset"] == 0
        assert res.data["content_has_more"] is False
        assert res.data["body_chars"] == 3

    async def test_middle_page_reports_more(self):
        body = "甲" * 5000 + "乙" * 5000
        res = await self._run(body, 4000, 2000)
        assert res.data["content"] == "甲" * 1000 + "乙" * 1000
        assert res.data["content_has_more"] is True
        assert res.data["content_offset"] == 4000
        assert res.data["body_chars"] == 10000

    async def test_past_end_page_empty_no_more(self):
        body = "abc"
        res = await self._run(body, 10, 100)
        assert res.data["content"] == ""
        assert res.data["content_has_more"] is False

    async def test_defaults_without_offset_params(self):
        body = "乙" * 100
        res = await self._run(body, None, None)
        assert res.data["content"] == body
        assert res.data["content_offset"] == 0
