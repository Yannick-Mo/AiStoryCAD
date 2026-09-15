"""读取工具（规格第 8 节，12 个）—— 范围读取、正文分页、伏笔清单。

五种访问形状：读一个节点 / 读子节点清单 / 读一个顺序范围 / 读一个决策用的聚合包 /
按关键词找。本文件覆盖其中的"读一个顺序范围"（``read_chapters`` →
``ContextBuilder.build_chapter_window``）、"读正文"（``read_chapter_content``）
与"读伏笔清单"（``list_setups``）。

取代了 ``test_range_tools.py``：``range_tools.py`` 模块已删除
（``read_recent_scenes`` / ``read_recent_chapters`` 并入 ``read_progress``，
``read_scene_content`` 改名为 ``read_chapter_content``）。
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


from app.agent.context import ContextBuilder
from app.agent.tools.read_tools import (
    ReadChapterContentTool,
    ReadChaptersTool,
    ListSetupsTool,
    SearchTool,
)


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

    def one(self):
        return self._rows[0]


class _ScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


def _chapter(uid, act_id, title="章", goal="", result="", status="draft", sort_order=1):
    return SimpleNamespace(id=uid, act_id=act_id, project_id=uuid.uuid4(), title=title,
                           goal=goal, result=result, status=status,
                           body_chars=0, sort_order=sort_order)


def _window_row(chapter, act_name, body_chars=0):
    return SimpleNamespace(id=chapter.id, title=chapter.title, goal=chapter.goal,
                           result=chapter.result, status=chapter.status,
                           sort_order=chapter.sort_order, act_id=chapter.act_id,
                           act_name=act_name, body_chars=body_chars)


def _owner_row():
    return SimpleNamespace(id=uuid.uuid4())


def _fake_db(*query_results):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(query_results))
    return db


# ---------------------------------------------------------------------------
# registration / meta
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_read_chapters_meta(self):
        assert ReadChaptersTool.meta.name == "read_chapters"
        assert ReadChaptersTool.meta.concurrency.value == "safe"
        assert ReadChaptersTool.meta.max_result_chars == 22000
        props = ReadChaptersTool.meta.parameters["properties"]
        assert {"chapter_from", "chapter_to"} <= set(props)
        assert ReadChaptersTool.meta.parameters["required"] == ["chapter_from", "chapter_to"]
        assert "全局章号" in ReadChaptersTool.meta.description

    def test_read_chapter_content_meta(self):
        tool = ReadChapterContentTool()
        assert tool.meta.name == "read_chapter_content"
        assert not tool.is_write_operation
        props = tool.meta.parameters["properties"]
        assert {"chapter_id", "content_offset", "content_limit"} <= set(props)
        assert tool.meta.parameters["required"] == ["chapter_id"]
        assert "正文" in tool.meta.description

    def test_list_setups_meta(self):
        tool = ListSetupsTool()
        assert tool.meta.name == "list_setups"
        assert not tool.is_write_operation
        assert tool.meta.parameters["properties"]["status"]["enum"] == ["open", "paid", "dropped"]

    def test_search_meta(self):
        tool = SearchTool()
        assert tool.meta.name == "search"
        assert not tool.is_write_operation
        assert tool.meta.parameters["required"] == ["keyword"]
        assert "正文" in tool.meta.description

    def test_read_surface_is_registered_in_both_modes(self):
        from app.agent.tool_filter import get_available_tools
        from app.agent.tools import get_tool_registry

        reg = get_tool_registry()
        names = {"read_project", "read_acts", "read_chapters", "read_chapter_content",
                 "read_chapter_package", "list_characters", "read_character",
                 "list_relations", "read_relation", "list_setups", "read_progress", "search"}
        assert names <= set(reg)
        for name in names:
            assert not reg[name].is_write_operation, name
        for mode in ("chat", "cowriter"):
            assert names <= set(get_available_tools(reg, mode=mode)), mode

    def test_deleted_read_tools_are_gone(self):
        from app.agent.tools import get_tool_registry

        reg = get_tool_registry()
        for name in ("read_scene", "read_scene_content", "read_chapter", "read_chapter_scenes",
                     "list_character_relations", "read_recent_chapters", "read_recent_scenes",
                     "read_global_settings", "read_project_overview", "list_chapters",
                     "list_scenes", "read_completed_tail"):
            assert name not in reg, name


# ---------------------------------------------------------------------------
# read_chapters 参数校验（不需要 DB）
# ---------------------------------------------------------------------------

class TestReadChaptersValidation:
    @patch("app.agent.tools.read_tools.verify_project_owner")
    @patch("app.agent.tools.read_tools.ContextBuilder")
    async def test_success_calls_window(self, mock_builder_cls, mock_verify):
        builder = MagicMock()
        builder.build_chapter_window = AsyncMock(return_value={"chapters": []})
        mock_builder_cls.return_value = builder
        res = await ReadChaptersTool().run(
            db=MagicMock(), project_id=str(uuid.uuid4()), user_id="u1",
            chapter_from=3, chapter_to=7,
        )
        assert res.success
        mock_verify.assert_awaited_once()
        builder.build_chapter_window.assert_awaited_once()
        args, _kwargs = builder.build_chapter_window.call_args
        assert args[1] == 3
        assert args[2] == 7

    # 参数校验发生在归属校验之前/之后都行，但这里不碰数据库：
    # 直接短路掉 verify_project_owner，免得 AsyncMock 的协程泄漏成 RuntimeWarning。
    @patch("app.agent.tools.read_tools.verify_project_owner")
    async def test_window_reversed_errors(self, mock_verify):
        res = await ReadChaptersTool().run(
            db=MagicMock(), project_id=str(uuid.uuid4()), user_id="u1",
            chapter_from=9, chapter_to=2)
        assert not res.success
        assert "起点大于终点" in res.error

    @patch("app.agent.tools.read_tools.verify_project_owner")
    async def test_non_positive_errors(self, mock_verify):
        res = await ReadChaptersTool().run(
            db=MagicMock(), project_id=str(uuid.uuid4()), user_id="u1",
            chapter_from=0, chapter_to=5)
        assert not res.success
        assert "从 1 开始" in res.error

    @patch("app.agent.tools.read_tools.verify_project_owner")
    async def test_non_integer_errors(self, mock_verify):
        res = await ReadChaptersTool().run(
            db=MagicMock(), project_id=str(uuid.uuid4()), user_id="u1",
            chapter_from="第一", chapter_to="第三")
        assert not res.success
        assert "整数" in res.error

    async def test_missing_project_id(self):
        res = await ReadChaptersTool().run(db=AsyncMock(), chapter_from=1, chapter_to=2)
        assert not res.success
        assert "project_id" in res.error



class TestChapterIndexMode:
    """粗读档（``detail="index"``）：长书扫目录用。

    它存在的理由是**规模**：3000 章的书用 ``full`` 一次只能读约 65 章（要 46 次才扫完），
    而一条 index 行只有 full 的约三分之一，同样预算能读三倍多的章。
    """

    def _db(self, chapters):
        return _fake_db(
            _QueryResult(rows=[(c.id,) for c in chapters]),
            _QueryResult(rows=[_window_row(c, "幕",
                                           body_chars=len(getattr(c, "content", "") or ""))
                               for c in chapters]),
        )

    async def test_index_rows_drop_plan_and_result(self):
        a1 = uuid.uuid4()
        ch = _chapter(uuid.uuid4(), a1, "章一", goal="计划" * 50, result="结果" * 50)
        ch.content = "正" * 100
        out = await ContextBuilder(self._db([ch])) \
            .build_chapter_window(uuid.uuid4(), 1, 1, detail="index")

        assert out["detail"] == "index"
        row = out["chapters"][0]
        assert set(row) == {"global_order", "id", "title", "status", "act_name", "written"}
        assert row["written"] is True, "粗读档也要能看出哪些章已经写了正文"
        assert row["title"] == "章一"

    async def test_index_mode_reads_much_more_in_the_same_budget(self):
        a1 = uuid.uuid4()
        chs = [_chapter(uuid.uuid4(), a1, f"第{i}章",
                        goal="这一章要写什么（计划）" * 10,
                        result="这一章写成了什么（结果）" * 10,
                        sort_order=i) for i in range(1, 401)]
        full = await ContextBuilder(self._db(chs)) \
            .build_chapter_window(uuid.uuid4(), 1, 400, detail="full")
        idx = await ContextBuilder(self._db(chs)) \
            .build_chapter_window(uuid.uuid4(), 1, 400, detail="index")

        assert full["truncated"], "400 章在 full 模式下该截断"
        assert idx["truncated"], "400 章在 index 模式下也该截断"
        ratio = len(idx["chapters"]) / len(full["chapters"])
        assert ratio >= 2.5, \
            f"index {len(idx['chapters'])} 章 vs full {len(full['chapters'])} 章（只有 {ratio:.1f} 倍）"

    async def test_default_detail_is_full(self):
        a1 = uuid.uuid4()
        ch = _chapter(uuid.uuid4(), a1, "章一", goal="计划", result="结果")
        out = await ContextBuilder(self._db([ch])).build_chapter_window(uuid.uuid4(), 1, 1)
        assert out["detail"] == "full"
        assert out["chapters"][0]["goal"] == "计划"


class TestReadChaptersDetailParam:
    def test_detail_is_an_explicit_enum(self):
        props = ReadChaptersTool.meta.parameters["properties"]
        assert props["detail"]["enum"] == ["full", "index"]

    @patch("app.agent.tools.read_tools.verify_project_owner")
    async def test_unknown_detail_is_rejected_with_the_two_valid_values(self, mock_verify):
        res = await ReadChaptersTool().run(
            db=MagicMock(), project_id=str(uuid.uuid4()), user_id="u1",
            chapter_from=1, chapter_to=2, detail="titles")
        assert not res.success
        assert "full" in res.error
        assert "index" in res.error

    @patch("app.agent.tools.read_tools.verify_project_owner")
    async def test_detail_reaches_the_builder(self, mock_verify):
        a1 = uuid.uuid4()
        ch = _chapter(uuid.uuid4(), a1, "章一", goal="计划", result="结果")
        db = _fake_db(_QueryResult(rows=[(ch.id,)]),
                      _QueryResult(rows=[_window_row(ch, "幕")]))
        res = await ReadChaptersTool().run(
            db=db, project_id=str(uuid.uuid4()), user_id="u1",
            chapter_from=1, chapter_to=1, detail="index")
        assert res.success, res.error
        assert res.data["detail"] == "index"
        assert "goal" not in res.data["chapters"][0]


# ---------------------------------------------------------------------------
# context builder: build_chapter_window
# ---------------------------------------------------------------------------

class TestBuildChapterWindow:
    def _window_db(self, chapters, acts):
        # 1) ordered chapter ids  2) window rows
        act_names = dict(acts)
        return _fake_db(
            _QueryResult(rows=[(c.id,) for c in chapters]),
            _QueryResult(rows=[
                _window_row(c, act_names.get(c.act_id, ""),
                            body_chars=len(getattr(c, "content", "") or ""))
                for c in chapters
            ]),
        )

    async def test_basic_window_carries_plan_result_and_act_name(self):
        a1, a2 = uuid.uuid4(), uuid.uuid4()
        chs = [
            _chapter(uuid.uuid4(), a1, "章一", goal="目标一", result="结果一"),
            _chapter(uuid.uuid4(), a2, "章二", goal="目标二", result="结果二"),
            _chapter(uuid.uuid4(), a2, "章三", goal="目标三", result=""),
        ]
        db = self._window_db(chs, [(a1, "第一幕"), (a2, "第二幕")])
        out = await ContextBuilder(db).build_chapter_window(uuid.uuid4(), 2, 3)

        assert out["total_chapters"] == 3
        assert out["truncated"] is False
        assert out["next_from"] is None
        assert [c["global_order"] for c in out["chapters"]] == [2, 3]
        first = out["chapters"][0]
        assert first["title"] == "章二"
        assert first["goal"] == "目标二"
        assert first["result"] == "结果二"
        assert first["act_name"] == "第二幕"
        assert first["act_id"] == str(a2)
        assert "body_chars" in first

    async def test_window_reports_body_chars_not_body(self):
        a1 = uuid.uuid4()
        ch = _chapter(uuid.uuid4(), a1, "章", goal="目标")
        ch.content = "正" * 42
        db = self._window_db([ch], [(a1, "幕")])
        out = await ContextBuilder(db).build_chapter_window(uuid.uuid4(), 1, 1)

        entry = out["chapters"][0]
        assert entry["body_chars"] == 42
        assert "content" not in entry, "范围读取不带正文——正文用 read_chapter_content"

    async def test_window_past_end_trims(self):
        a1 = uuid.uuid4()
        chs = [_chapter(uuid.uuid4(), a1, "只有一章", goal="g")]
        out = await ContextBuilder(self._window_db(chs, [(a1, "幕")])) \
            .build_chapter_window(uuid.uuid4(), 1, 99)
        assert out["chapters"][0]["global_order"] == 1
        assert out["total_chapters"] == 1

    async def test_window_outside_range_is_empty(self):
        a1 = uuid.uuid4()
        chs = [_chapter(uuid.uuid4(), a1, "只有一章", goal="g")]
        out = await ContextBuilder(self._window_db(chs, [(a1, "幕")])) \
            .build_chapter_window(uuid.uuid4(), 5, 7)
        assert out["chapters"] == []
        assert out["total_chapters"] == 1

    async def test_window_reversed_range_returns_empty_payload(self):
        out = await ContextBuilder(_fake_db(_QueryResult(rows=[]))) \
            .build_chapter_window(uuid.uuid4(), 4, 2)
        assert out["chapters"] == []
        assert out["truncated"] is False

    async def test_budget_trims_with_next_from(self):
        a1 = uuid.uuid4()
        chs = [
            _chapter(uuid.uuid4(), a1, "章一", goal="短"),
            _chapter(uuid.uuid4(), a1, "章二", goal="目标" * 3000),   # ~6k chars
            _chapter(uuid.uuid4(), a1, "章三", goal="目标" * 3000),
        ]
        out = await ContextBuilder(self._window_db(chs, [(a1, "幕")])) \
            .build_chapter_window(uuid.uuid4(), 1, 3, budget_chars=500)
        assert out["truncated"] is True
        assert out["next_from"] == 2
        assert len(out["chapters"]) == 1


# ---------------------------------------------------------------------------
# read_chapter_content pagination (run level)
# ---------------------------------------------------------------------------

class TestReadChapterContentPagination:
    async def _run(self, body, offset=None, limit=None):
        pid, cid = uuid.uuid4(), uuid.uuid4()
        chapter = SimpleNamespace(id=cid, project_id=pid, title="第一章", content=body)
        db = _fake_db(
            _QueryResult(scalars=[chapter]),
            _QueryResult(scalars=[_owner_row()]),
        )
        kwargs = {"project_id": str(pid), "user_id": "u1", "chapter_id": str(cid)}
        if offset is not None:
            kwargs["content_offset"] = offset
        if limit is not None:
            kwargs["content_limit"] = limit
        return await ReadChapterContentTool().run(db, **kwargs)

    async def test_first_page_no_more(self):
        res = await self._run("短正文", 0, 6000)
        assert res.success
        assert res.data["content"] == "短正文"
        assert res.data["content_offset"] == 0
        assert res.data["content_has_more"] is False
        assert res.data["body_chars"] == 3
        assert res.data["chapter_title"] == "第一章"

    async def test_middle_page_reports_more(self):
        body = "甲" * 5000 + "乙" * 5000
        res = await self._run(body, 4000, 2000)
        assert res.data["content"] == "甲" * 1000 + "乙" * 1000
        assert res.data["content_has_more"] is True
        assert res.data["content_offset"] == 4000
        assert res.data["body_chars"] == 10000

    async def test_past_end_page_is_empty_and_truthfully_done(self):
        res = await self._run("abc", 10, 100)
        assert res.data["content"] == ""
        assert res.data["content_has_more"] is False

    async def test_defaults_without_offset_params_read_the_whole_body(self):
        body = "乙" * 100
        res = await self._run(body)
        assert res.data["content"] == body
        assert res.data["content_offset"] == 0
        assert res.data["content_has_more"] is False

    async def test_negative_offset_is_clamped_to_zero(self):
        res = await self._run("abcdef", -5, 3)
        assert res.data["content"] == "abc"
        assert res.data["content_offset"] == 0

    async def test_unknown_chapter_errors(self):
        db = _fake_db(_QueryResult(scalars=[]))
        res = await ReadChapterContentTool().run(
            db, user_id="u1", chapter_id=str(uuid.uuid4()))
        assert not res.success
        assert "章节" in res.error

    async def test_bad_uuid_id_is_taught_not_raised(self):
        res = await ReadChapterContentTool().run(
            AsyncMock(), user_id="u1", chapter_id="not-a-uuid")
        assert not res.success
        assert "UUID" in res.error or "不是合法" in res.error


# ---------------------------------------------------------------------------
# list_setups
# ---------------------------------------------------------------------------

def _setup(note, chapter_id, status="open", pay_off_in=None):
    return SimpleNamespace(id=uuid.uuid4(), note=note, status=status,
                           planted_in=chapter_id, pay_off_in=pay_off_in)


class TestListSetups:
    def _db(self, setups, chapters):
        # 执行顺序与 ListSetupsTool.run 一致：验归属 → 取伏笔 → _chapter_index
        return _fake_db(
            _QueryResult(scalars=[_owner_row()]),                       # verify owner
            _QueryResult(scalars=setups),                               # setups
            _QueryResult(rows=[(cid,) for cid, _t in chapters]),        # ordered chapter ids
            _QueryResult(rows=list(chapters)),                          # id → title
        )

    async def test_rows_resolve_chapter_refs_to_global_order(self):
        pid = uuid.uuid4()
        ch1, ch2 = uuid.uuid4(), uuid.uuid4()
        setups = [_setup("旧硬币", ch1, "open", pay_off_in=ch2),
                  _setup("一封信", ch1, "open", pay_off_in=None)]
        db = self._db(setups, [(ch1, "第一章"), (ch2, "第二章")])

        res = await ListSetupsTool().run(db, project_id=str(pid), user_id="u1")
        assert res.success
        assert res.data["count"] == 2
        row = res.data["setups"][0]
        assert set(row) == {"id", "note", "status", "planted_in", "pay_off_in"}
        assert row["planted_in"] == {"chapter_id": str(ch1), "global_order": 1, "title": "第一章"}
        assert row["pay_off_in"]["global_order"] == 2
        assert res.data["setups"][1]["pay_off_in"] == {
            "chapter_id": None, "global_order": None, "title": ""}

    async def test_invalid_status_is_rejected_before_querying(self):
        res = await ListSetupsTool().run(
            _fake_db(_QueryResult(scalars=[_owner_row()])),
            project_id=str(uuid.uuid4()), user_id="u1", status="半收")
        assert not res.success
        assert "open" in res.error
        assert "paid" in res.error
        assert "dropped" in res.error

    async def test_status_filter_is_accepted(self):
        pid = uuid.uuid4()
        ch = uuid.uuid4()
        db = self._db([_setup("刀", ch, "paid")], [(ch, "第一章")])
        res = await ListSetupsTool().run(db, project_id=str(pid), user_id="u1", status="paid")
        assert res.success
        assert res.data["setups"][0]["status"] == "paid"
