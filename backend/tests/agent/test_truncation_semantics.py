"""结果截断与分页的语义（batch B 的修复，随重构迁到章）。

1. 长文本字段（content / goal / result …）保留**头与尾**，绝不塌缩成 200 字摘要。
2. ID 清单语义：``read_chapters`` 这类清单工具的数组保留**全部条目**，
   一章 30 章不会被悄悄砍成 3 章。
3. 正文/设定按需整篇返回（单次结果上限 ~10 万字符）；分页只在超过上限时才介入——
   不会出现"只给 200 字却声称已读完"。
4. 「有没有正文」只有一个定义，读正文与读范围两个表面必须一致。
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.tools.base import ToolResult
from app.agent.tools import streaming_executor as se


class _Q:
    def __init__(self, rows=None, scalars=None):
        self._rows = rows if rows is not None else []
        self._scalars = scalars if scalars is not None else []

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None


def _fake_db(*results):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(results))
    db.commit = AsyncMock()
    return db


class TestLongTextHeadTail:
    def test_content_field_keeps_head_and_tail(self):
        body = "序章开头" + "中间内容" * 3000 + "大结局"
        payload = json.dumps({"chapter_id": str(uuid.uuid4()), "content": body,
                              "meta": "x" * 600})
        out = se._smart_summarise(payload, 8000, "read_chapter_content")
        assert out.startswith("{")
        assert "序章开头" in out
        assert "大结局" in out
        assert "省略" in out
        assert "全文共" in out
        collapsed = json.loads(out)
        assert collapsed["meta"].startswith("x" * 200)
        assert collapsed["content"].endswith("大结局")

    def test_a_non_payload_key_gets_a_short_digest(self):
        """不是正文/蓝图的字段没有保留结尾的道理，直接给 200 字摘要。"""
        payload = json.dumps({"meta": "x" * 3000})
        out = se._smart_summarise(payload, 1000, "some_tool")
        parsed = json.loads(out)
        assert len(parsed["meta"]) < 300
        assert "[3000 chars]" in parsed["meta"]

    def test_payload_keys_keep_their_tail(self):
        """``note`` / ``goal`` / ``result`` 都属于"载荷本身"，必须留结尾。"""
        payload = json.dumps({"note": "起" + "中" * 3000 + "尾"})
        out = se._smart_summarise(payload, 1000, "some_tool")
        assert json.loads(out)["note"].endswith("尾")


class TestIdListKeepsEveryEntry:
    """清单类工具的结果是导航地图：少一条就等于模型看不见那一章。"""

    def test_read_chapters_keeps_every_chapter_entry(self):
        tool = SimpleNamespace(_effective_max_result_chars=8000)
        chapters = [
            {"id": str(uuid.uuid4()), "title": f"第{i}章", "sort_order": i + 1,
             "goal": "本章要写什么" * 200}
            for i in range(30)
        ]
        out = se._summarise_tool_output(
            "read_chapters",
            ToolResult(success=True, data={"chapters": chapters, "chapter_count": 30}),
            tool,
        )
        parsed = json.loads(out["data"])
        assert len(parsed["chapters"]) == 30, "ID 清单语义：一条都不能丢"
        assert parsed["chapters"][29]["title"] == "第29章"
        assert parsed["chapters"][0]["goal"].endswith("chars]"), "每条的正文摘要被压缩了"

    def test_a_non_list_tool_keeps_only_the_first_three(self):
        """非清单工具仍然只保留前 3 段，免得一个很大的聚合包炸掉上下文。"""
        tool = SimpleNamespace(_effective_max_result_chars=400)
        items = [{"id": str(uuid.uuid4()), "note": f"伏笔{i}"} for i in range(10)]
        out = se._summarise_tool_output(
            "some_aggregate_tool", ToolResult(success=True, data={"rows": items}), tool)
        assert "10 项" in out["data"], out["data"]


class TestSinglePageClamp:
    async def _read_content(self, body, offset=None, limit=None):
        from app.agent.tools.read_tools import ReadChapterContentTool

        pid, cid = uuid.uuid4(), uuid.uuid4()
        chapter = SimpleNamespace(id=cid, project_id=pid, title="第一章", content=body)
        db = _fake_db(_Q(scalars=[chapter]), _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]))
        kwargs = {"project_id": str(pid), "user_id": "u", "chapter_id": str(cid)}
        if offset is not None:
            kwargs["content_offset"] = offset
        if limit is not None:
            kwargs["content_limit"] = limit
        return await ReadChapterContentTool().run(db, **kwargs)

    async def test_default_reads_the_whole_body(self):
        body = "字" * 30000
        res = await self._read_content(body)
        assert res.success
        assert res.data["content"] == body       # full 30k, no pagination tax
        assert res.data["content_has_more"] is False
        assert res.data["body_chars"] == 30000

    async def test_offsets_still_work(self):
        res = await self._read_content("abcdefgh", 3, 4)
        assert res.data["content"] == "defg"
        assert res.data["content_has_more"] is True

    async def test_absurdly_long_body_clamped_with_true_has_more(self):
        # Beyond the ~100k single-result ceiling pagination takes over — the
        # has_more flag must stay truthful (never "200 chars but finished").
        body = "字" * 300000
        res = await self._read_content(body)
        assert res.success
        assert len(res.data["content"]) <= 99900
        assert res.data["content_has_more"] is True

    async def test_project_global_settings_are_not_paginated(self):
        """设定只有「整篇给」和「不给」两种——分页参数只属于正文。"""
        from app.agent.tools.read_tools import ReadProjectTool

        pid = uuid.uuid4()
        project = SimpleNamespace(
            id=pid, title="T", genre="", logline="", spine="",
            global_settings="设" * 25000, total_words=100000, status="init")
        db = _fake_db(_Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),
                      _Q(scalars=[project]))

        res = await ReadProjectTool().run(db, project_id=str(pid), user_id="u")
        assert res.success
        assert res.data["global_settings"] == "设" * 25000
        assert "content_has_more" not in res.data
        assert "content_offset" not in res.data


class TestWrittenHasOneDefinition:
    """「这一章有没有正文」只有一个定义：正文字符数 > 0。

    两个只读表面（读正文 / 读范围）必须给出同一个答案，否则模型会一边看到
    「已写」、一边读到空正文。
    """

    async def test_body_chars_agree_between_the_two_read_surfaces(self, db_session, test_user):

        from app.agent.context import ContextBuilder
        from app.agent.tools.read_tools import ReadChapterContentTool
        from app.project.repository import ProjectRepository
        from app.storycad.models import Act, Chapter

        project = await ProjectRepository(db_session).create("Trunc", "", test_user["id"])
        act = Act(id=uuid.uuid4(), project_id=project.id, name="第一幕",
                  purpose="甲", sort_order=1)
        db_session.add(act)
        written = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                          title="第一章", goal="要写什么", content="正文" * 20,
                          sort_order=1)
        blank = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                        title="第二章", goal="要写什么", content="",
                        sort_order=2)
        db_session.add_all([written, blank])
        await db_session.flush()

        window = await ContextBuilder(db_session).build_chapter_window(project.id, 1, 2)
        by_order = {entry["global_order"]: entry for entry in window["chapters"]}
        assert by_order[1]["body_chars"] == len("正文" * 20)
        assert by_order[2]["body_chars"] == 0

        for chapter_id, expected in ((written.id, len("正文" * 20)), (blank.id, 0)):
            res = await ReadChapterContentTool().run(
                db_session, project_id=str(project.id), user_id=str(test_user["id"]),
                chapter_id=str(chapter_id))
            assert res.data["body_chars"] == expected
