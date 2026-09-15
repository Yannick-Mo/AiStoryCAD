"""正文写入路径返回的即时统计，以及 base._err 的教学式错误翻译。

旧版这里断言的是「写入后刷新章节聚合」（scene_count / total_words）——
那些派生列已随重构删除（规格第 10 节）。字数现在**实时算**，不落库，
所以正文写入工具要做的是把新的字数如实回报给调用方，而不是维护一个可能过时的聚合列。
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.tools.base import BaseTool
from app.agent.tools.content_tools import ContinueChapterTool, WriteChapterTool


class _Q:
    def __init__(self, rows=None, scalars=None, one_row=None):
        self._rows = rows if rows is not None else []
        self._scalars = scalars if scalars is not None else []
        self._one = one_row

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None

    def one(self):
        return self._one


def _db(chapter):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Q(scalars=[chapter]),                          # select Chapter
        _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),  # verify_project_owner
    ])
    db.commit = AsyncMock()
    return db


class TestContentWriteToolsReturnFreshCounts:
    """没有派生列了：字数由正文实时算出并随结果返回。"""

    async def test_write_chapter_replaces_the_body_and_reports_counts(self):
        pid = uuid.uuid4()
        chapter = SimpleNamespace(id=uuid.uuid4(), project_id=pid, title="第一章",
                                  content="旧正文" * 10)
        db = _db(chapter)

        res = await WriteChapterTool().run(
            db, project_id=str(pid), user_id="u",
            chapter_id=str(chapter.id), content="新正文内容")

        assert res.success, res.error
        assert chapter.content == "新正文内容", "write_chapter 是整体替换"
        assert res.data["word_count"] == 5
        assert res.data["body_chars"] == 5
        assert res.data["replaced_body_chars"] == len("旧正文" * 10)
        assert "update_chapter" in res.data["reminder"], "写完必须提醒补「本章写成了什么」"
        db.commit.assert_awaited()

    async def test_continue_chapter_appends_and_reports_counts(self):
        pid = uuid.uuid4()
        chapter = SimpleNamespace(id=uuid.uuid4(), project_id=pid, title="第一章",
                                  content="前面的正文。")
        db = _db(chapter)

        res = await ContinueChapterTool().run(
            db, project_id=str(pid), user_id="u",
            chapter_id=str(chapter.id), additional_content="后来的正文。")

        assert res.success, res.error
        assert chapter.content == "前面的正文。后来的正文。", "追加不加分隔符"
        assert res.data["appended_chars"] == len("后来的正文。")
        assert res.data["body_chars"] == len("前面的正文。后来的正文。")
        assert res.data["word_count"] == 11, "10 个汉字 + 结尾标点算 1 个非中文词"

    async def test_continue_chapter_counts_the_whole_body_not_just_the_addition(self):
        pid = uuid.uuid4()
        chapter = SimpleNamespace(id=uuid.uuid4(), project_id=pid, title="第一章",
                                  content="字" * 100)
        db = _db(chapter)

        res = await ContinueChapterTool().run(
            db, project_id=str(pid), user_id="u",
            chapter_id=str(chapter.id), additional_content="字" * 5)

        assert res.data["appended_chars"] == 5
        assert res.data["word_count"] == 105, "续写后的字数必须是全章字数"
        assert res.data["body_chars"] == 105

    async def test_write_chapter_refuses_an_empty_body(self):
        pid = uuid.uuid4()
        chapter = SimpleNamespace(id=uuid.uuid4(), project_id=pid, title="第一章",
                                  content="旧正文")
        res = await WriteChapterTool().run(
            db=_db(chapter), project_id=str(pid), user_id="u",
            chapter_id=str(chapter.id), content="   ")
        assert not res.success
        assert "content" in res.error
        assert chapter.content == "旧正文", "空正文不能把原文冲掉"

    async def test_continue_chapter_refuses_an_empty_addition(self):
        pid = uuid.uuid4()
        chapter = SimpleNamespace(id=uuid.uuid4(), project_id=pid, title="第一章",
                                  content="旧正文")
        res = await ContinueChapterTool().run(
            db=_db(chapter), project_id=str(pid), user_id="u",
            chapter_id=str(chapter.id), additional_content="")
        assert not res.success
        assert "additional_content" in res.error
        assert chapter.content == "旧正文"

    async def test_unknown_chapter_errors(self):
        pid = uuid.uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_Q(scalars=[])])
        db.rollback = AsyncMock()
        res = await WriteChapterTool().run(
            db, project_id=str(pid), user_id="u",
            chapter_id=str(uuid.uuid4()), content="正文")
        assert not res.success
        assert "章节" in res.error

    def test_body_write_tools_are_destructive_exclusive_writes(self):
        for tool in (WriteChapterTool(), ContinueChapterTool()):
            assert tool.is_write_operation, type(tool).__name__
        assert WriteChapterTool().meta.is_destructive, "整体替换会覆盖旧正文"


class TestErrTeaching:
    async def test_uuid_error_translated(self):
        res = BaseTool._err(ValueError("badly formed hexadecimal UUID string"))
        assert res.success is False
        assert "不是合法的 UUID" in res.error
        assert res.correction_hint

    def test_permission_error_translated(self):
        res = BaseTool._err(PermissionError("User u1 does not own project p"))
        assert "不属于当前用户" in res.error
        assert res.correction_hint

    def test_other_errors_pass_through(self):
        res = BaseTool._err(RuntimeError("数据库连接失败"))
        assert res.error == "数据库连接失败"
