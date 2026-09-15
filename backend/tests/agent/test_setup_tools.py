"""伏笔工具：``delete_setup`` 的语义边界（规格第 9 节 + 第 13.5 节）。

伏笔有**两种「不在了」**，不能混：

* 不打算收了 → ``update_setup`` 把 status 改成 ``dropped``（那是一条信息：「我放弃收它了」）；
* 这条记录根本不该存在（建错 / 建重）→ ``delete_setup`` 删掉它。

拿 ``dropped`` 表达「建错了」是语义污染：``read_progress`` 的伏笔账目里会永远挂着
一条本不该存在的记录。这条边界就是本文件的判据。
"""
import uuid
from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.tools.setup_tools import DeleteSetupTool, UpdateSetupTool
from app.storycad.models import Setup


class _Q:
    def __init__(self, scalars=None):
        self._scalars = scalars if scalars is not None else []

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None

    def scalars(self):
        return self

    def all(self):
        return self._scalars


def _setup(note="旧硬币", status="open"):
    return Setup(
        id=uuid.uuid4(), project_id=uuid.uuid4(), note=note,
        planted_in=uuid.uuid4(), pay_off_in=None, status=status,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def _owner():
    return SimpleNamespace(id=uuid.uuid4())


class TestDeleteSetup:
    def _db(self, setup, owner=True):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[setup]),                       # select setup
            _Q(scalars=[_owner()] if owner else []),    # verify_project_owner
        ])
        return db

    async def test_deletes_the_row_and_reports_the_evicted_id(self):
        s = _setup()
        db = self._db(s)
        res = await DeleteSetupTool().run(
            db, project_id=str(s.project_id), user_id="u1", setup_id=str(s.id))
        assert res.success, res.error
        assert res.data["deleted_setup_id"] == str(s.id)
        assert res.data["note"] == "旧硬币"
        db.delete.assert_awaited_once_with(s)
        db.commit.assert_awaited()

    async def test_missing_setup_id_points_at_list_setups(self):
        res = await DeleteSetupTool().run(AsyncMock(), user_id="u1")
        assert not res.success
        assert "setup_id" in res.error
        assert "list_setups" in res.correction_hint

    async def test_unknown_setup_id_is_not_found_not_a_crash(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_Q(scalars=[])])
        res = await DeleteSetupTool().run(
            db, project_id=str(uuid.uuid4()), user_id="u1", setup_id=str(uuid.uuid4()))
        assert not res.success
        assert "伏笔" in res.error
        db.delete.assert_not_called()

    async def test_cross_project_delete_is_refused_without_touching_the_row(self):
        s = _setup()
        db = self._db(s, owner=False)
        res = await DeleteSetupTool().run(
            db, project_id=str(s.project_id), user_id="other-user", setup_id=str(s.id))
        assert not res.success
        assert "归属" in res.error
        # 归属校验失败时绝不能碰这一行
        db.delete.assert_not_called()

    async def test_malformed_uuid_is_a_teaching_error(self):
        db = AsyncMock()
        res = await DeleteSetupTool().run(
            db, project_id=str(uuid.uuid4()), user_id="u1", setup_id="not-a-uuid")
        assert not res.success
        assert "list_setups" in res.error or "list_setups" in (res.correction_hint or "")

    def test_only_takes_a_setup_id(self):
        assert set(DeleteSetupTool.meta.parameters["properties"]) == {"setup_id"}
        assert DeleteSetupTool.meta.parameters["required"] == ["setup_id"]

    def test_is_destructive_and_needs_confirmation(self):
        tool = DeleteSetupTool()
        assert tool.meta.is_destructive
        assert tool.meta.needs_confirmation
        assert tool.is_write_operation


class TestDropVersusDeleteBoundary:
    """两个工具的名字要各自说清边界，模型才选得对。"""

    def test_update_setup_owns_dropped(self):
        desc = UpdateSetupTool.meta.description
        assert "dropped" in desc
        assert "不打算收了" in desc

    def test_delete_setup_tells_the_model_not_to_use_it_for_dropped(self):
        desc = DeleteSetupTool.meta.description
        assert "dropped" in desc
        assert "不打算收了不要用这个" in desc.replace("*", "")

    def test_registry_exposes_both(self):
        from app.agent.tools import get_tool_registry

        reg = get_tool_registry()
        assert {"create_setup", "update_setup", "delete_setup"} <= set(reg)
