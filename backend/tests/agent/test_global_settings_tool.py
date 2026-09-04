"""Tests for global-settings reading: read_global_settings (full text,
paginated) and read_project (metadata only + settings length hint)."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent.tools.project_tools import ReadProjectTool, ReadGlobalSettingsTool


class _Q:
    def __init__(self, rows=None, scalars=None):
        self._rows = rows if rows is not None else []
        self._scalars = scalars if scalars is not None else []

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None


def _make_db(project=None):
    pid = uuid.uuid4()
    owner = SimpleNamespace(id=uuid.uuid4())
    project = project or SimpleNamespace(
        id=pid,
        title="测试小说",
        global_settings="世界观全文：魔法大陆的秩序由五大元素议会维持。" * 30,
    )
    config = SimpleNamespace(id=uuid.uuid4(), project_id=pid)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Q(scalars=[owner]),     # verify_project_owner
        _Q(scalars=[project]),   # repo.get
        _Q(scalars=[config]),    # repo.get_config (read_project only)
    ])
    return db, pid


class TestReadGlobalSettings:
    async def _run(self, offset=None, limit=None):
        db, pid = _make_db()
        kwargs = {"project_id": str(pid), "user_id": "u1"}
        if offset is not None:
            kwargs["content_offset"] = offset
        if limit is not None:
            kwargs["content_limit"] = limit
        return await ReadGlobalSettingsTool().run(db, **kwargs)

    async def test_first_page(self):
        body = "甲" * 200
        pid = uuid.uuid4()
        db, _ = _make_db(SimpleNamespace(id=pid, title="T", global_settings=body))
        res = await ReadGlobalSettingsTool().run(
            db, project_id=str(pid), user_id="u1", content_offset=0, content_limit=6000)
        assert res.success
        assert res.data["content"] == body
        assert res.data["settings_chars"] == 200
        assert res.data["content_has_more"] is False
        assert res.data["content_offset"] == 0

    async def test_pagination_middle_page(self):
        body = "甲" * 5000 + "乙" * 5000
        pid = uuid.uuid4()
        db, _ = _make_db(SimpleNamespace(id=pid, title="T", global_settings=body))
        res = await ReadGlobalSettingsTool().run(
            db, project_id=str(pid), user_id="u1", content_offset=4000, content_limit=2000)
        assert res.data["content"] == "甲" * 1000 + "乙" * 1000
        assert res.data["content_has_more"] is True
        assert res.data["settings_chars"] == 10000

    async def test_defaults_and_past_end(self):
        body = "短设定"
        pid = uuid.uuid4()
        db, _ = _make_db(SimpleNamespace(id=pid, title="T", global_settings=body))
        res = await ReadGlobalSettingsTool().run(db, project_id=str(pid), user_id="u1")
        assert res.data["content"] == body
        pid2 = uuid.uuid4()
        db2, _ = _make_db(SimpleNamespace(id=pid2, title="T", global_settings=body))
        res2 = await ReadGlobalSettingsTool().run(
            db2, project_id=str(pid2), user_id="u1", content_offset=99, content_limit=100)
        assert res2.data["content"] == ""
        assert res2.data["content_has_more"] is False

    async def test_registered_readonly(self):
        from app.agent.tools import get_tool_registry, get_filtered_tools
        reg = get_tool_registry()
        assert "read_global_settings" in reg
        assert not reg["read_global_settings"].is_write_operation
        chat = get_filtered_tools(reg, mode="chat")
        assert "read_global_settings" in chat


class TestReadProjectMetaOnly:
    async def test_global_settings_excluded_with_char_hint(self):
        db, pid = _make_db()
        with patch("app.agent.tools.project_tools.row_to_dict",
                   side_effect=[
                       {"id": str(pid), "title": "测试小说",
                        "global_settings": "x" * 1000},       # project row
                       {"id": str(uuid.uuid4())},              # config row
                   ]):
            res = await ReadProjectTool().run(db, project_id=str(pid), user_id="u1")
        assert res.success
        assert "global_settings" not in res.data
        assert res.data["global_settings_chars"] == 1000

    def test_description_points_to_settings_tool(self):
        assert "read_global_settings" in ReadProjectTool.meta.description
        props = ReadGlobalSettingsTool.meta.parameters["properties"]
        assert {"content_offset", "content_limit"} <= set(props)
