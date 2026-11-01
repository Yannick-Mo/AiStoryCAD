"""``update_project``：作品层只保留当前已完成骨架 / 当前全局设定，并支持往后追加。"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.tools.project_tools import UpdateProjectTool


class _Q:
    def __init__(self, scalars=None):
        self._scalars = scalars or []

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None


def _project(**overrides):
    data = dict(
        id=uuid.uuid4(), title="归乡", genre="悬疑", logline="一句话",
        spine="", global_settings="", total_words=100000,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _db(project):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Q([SimpleNamespace(id=uuid.uuid4())]))
    db.get = AsyncMock(return_value=project)
    return db


class TestUpdateProjectSurface:
    def test_ending_is_gone_from_the_tool_schema(self):
        props = UpdateProjectTool.meta.parameters["properties"]
        assert "ending" not in props
        assert "spine" in props
        assert "spine_append" in props
        assert "global_settings_append" in props

    def test_description_does_not_promise_a_full_book_plan(self):
        desc = UpdateProjectTool.meta.description
        assert "当前已完成骨架" in desc
        assert "全书大纲" in desc
        assert "spine_append" in desc


class TestAppendNeverOverwrites:
    async def test_spine_append_keeps_existing_nodes(self):
        project = _project(spine="奔丧 → 账本缺页")
        res = await UpdateProjectTool().run(
            _db(project), project_id=str(project.id), user_id="u1",
            spine_append="姑妈改口 → 旧债浮出")

        assert res.success, res.error
        assert project.spine == "奔丧 → 账本缺页\n姑妈改口 → 旧债浮出"
        assert res.data["spine"] == project.spine
        assert res.data["updated_fields"] == ["spine_append"]

    async def test_global_settings_append_keeps_existing_text(self):
        project = _project(global_settings="小镇叫渡口。")
        res = await UpdateProjectTool().run(
            _db(project), project_id=str(project.id), user_id="u1",
            global_settings_append="镇上有座废弃的码头。")

        assert res.success, res.error
        assert project.global_settings == "小镇叫渡口。\n镇上有座废弃的码头。"
        assert res.data["updated_fields"] == ["global_settings_append"]

    async def test_empty_append_is_a_noop_but_still_counts_as_a_field(self):
        project = _project(spine="奔丧")
        res = await UpdateProjectTool().run(
            _db(project), project_id=str(project.id), user_id="u1",
            spine_append="   ")

        assert res.success, res.error
        assert project.spine == "奔丧"

    async def test_ending_cannot_be_written_even_if_a_model_sends_it(self):
        project = _project(spine="奔丧")
        project.ending = "旧终局"
        res = await UpdateProjectTool().run(
            _db(project), project_id=str(project.id), user_id="u1",
            ending="新终局")

        assert not res.success
        assert project.ending == "旧终局", "废弃字段不能被工具悄悄改写"
