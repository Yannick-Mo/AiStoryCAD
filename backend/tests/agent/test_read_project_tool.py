"""``read_project`` —— 作品层一次读完（书名 / 类型 / 梗概 / 终局 / 主线骨架 / 全局设定）。

``read_global_settings`` 已并入 ``read_project``（规格第 10 节的合并清单）：
全局设定只剩一个消费者（写作循环第 3 步「这一章在整本书里的位置」），
不值得为它单独留一个工具，也不需要分页——单次结果上限约 10 万字符，
一本书的设定很难到那儿。分页只留给正文（``read_chapter_content``）。
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock


from app.agent.tools.read_tools import ReadProjectTool


class _Q:
    def __init__(self, rows=None, scalars=None):
        self._rows = rows if rows is not None else []
        self._scalars = scalars if scalars is not None else []

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None


def _make_db(project=None):
    pid = getattr(project, "id", None) or uuid.uuid4()
    if project is None:
        project = SimpleNamespace(
            id=pid, title="测试小说", genre="悬疑", logline="一句话梗概",
            ending="终局", spine="主线骨架",
            global_settings="世界观全文：魔法大陆的秩序由五大元素议会维持。",
            total_words=90000, status="init",
        )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),   # verify_project_owner
        _Q(scalars=[project]),                            # select(Project)
    ])
    return db, project


class TestReadProject:
    async def _run(self, project=None):
        db, project = _make_db(project)
        return await ReadProjectTool().run(
            db, project_id=str(project.id), user_id="u1")

    async def test_returns_every_work_level_field(self):
        res = await self._run()
        assert res.success, res.error
        assert res.data["title"] == "测试小说"
        assert res.data["genre"] == "悬疑"
        assert res.data["logline"] == "一句话梗概"
        assert res.data["ending"] == "终局"
        assert res.data["spine"] == "主线骨架"
        assert res.data["total_words"] == 90000
        assert res.data["status"] == "init"

    async def test_global_settings_is_returned_in_full(self):
        body = "甲" * 25000
        res = await self._run(SimpleNamespace(
            id=uuid.uuid4(), title="T", genre="", logline="", ending="", spine="",
            global_settings=body, total_words=100000, status="init"))

        assert res.data["global_settings"] == body
        assert res.data["global_settings_chars"] == 25000

    async def test_char_counts_are_reported_for_the_big_text_fields(self):
        body = "设" * 4321
        res = await self._run(SimpleNamespace(
            id=uuid.uuid4(), title="T", genre="", logline="", ending="终" * 12,
            spine="", global_settings=body, total_words=100000, status="init"))

        assert res.data["global_settings_chars"] == 4321
        assert res.data["ending_chars"] == 12

    async def test_empty_project_text_fields_come_back_as_empty_strings(self):
        res = await self._run(SimpleNamespace(
            id=uuid.uuid4(), title="T", genre=None, logline=None, ending=None,
            spine=None, global_settings=None, total_words=None, status=None))

        assert res.data["genre"] == ""
        assert res.data["logline"] == ""
        assert res.data["ending"] == ""
        assert res.data["spine"] == ""
        assert res.data["global_settings"] == ""
        assert res.data["global_settings_chars"] == 0
        assert res.data["total_words"] == 0

    async def test_missing_project_id_errors(self):
        db, _project = _make_db()
        res = await ReadProjectTool().run(db, user_id="u1")
        assert not res.success
        assert "project_id" in res.error

    async def test_unknown_project_errors(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),
            _Q(scalars=[]),
        ])
        res = await ReadProjectTool().run(
            db, project_id=str(uuid.uuid4()), user_id="u1")
        assert not res.success
        assert "项目" in res.error

    async def test_foreign_project_is_denied(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_Q(scalars=[])])
        res = await ReadProjectTool().run(
            db, project_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))
        assert not res.success
        assert "归属" in res.error or "不属于" in res.error


class TestToolSurface:
    def test_registered_readonly_in_both_modes(self):
        from app.agent.tools import get_filtered_tools, get_tool_registry

        reg = get_tool_registry()
        assert "read_project" in reg
        assert not reg["read_project"].is_write_operation
        for mode in ("chat", "cowriter"):
            assert "read_project" in get_filtered_tools(reg, mode=mode), mode

    def test_read_global_settings_is_gone(self):
        from app.agent.tools import get_tool_registry

        assert "read_global_settings" not in get_tool_registry()

    def test_takes_no_parameters(self):
        """项目身份由会话注入；全局设定没有分页参数（那是正文才需要的）。"""
        tool = ReadProjectTool()
        props = tool.meta.parameters["properties"]
        assert props == {}
        for absent in ("content_offset", "content_limit", "project_id", "include_content"):
            assert absent not in props, absent

    def test_description_promises_the_full_settings_text(self):
        desc = ReadProjectTool.meta.description
        assert "全局设定" in desc
        assert "全文" in desc
        assert "read_chapter_content" in desc, "正文不在这里，必须指出正确的工具"
