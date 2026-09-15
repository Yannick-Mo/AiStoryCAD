"""名词录类工具：``list_relations``（浏览）与 ``read_relation``（精读一条）。

角色关系是一条条**记录**，不是一张图。三个强度值（trust / threat / attraction）
已删除：它们是伪精度，而且关系强度随剧情变化属于动态——静态记录里放动态值必然过时。
关系的变化写进那一章的「本章写成了什么」。

``list_character_relations`` 已并入 ``list_relations``（规格第 10 节），
``list_tools.py`` 模块本身也不再存在——这些工具现在住在 ``read_tools.py``。
"""
import uuid
from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.tools.read_tools import ListRelationsTool, ReadRelationTool
from app.storycad.models import CharacterRelation


class _Q:
    def __init__(self, rows=None, scalars=None):
        self._rows = rows if rows is not None else []
        self._scalars = scalars if scalars is not None else []

    def all(self):
        return self._rows

    def scalars(self):
        return _S(self._scalars)

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None

    def __iter__(self):
        return iter(self._rows)


class _S:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


def _relation(rid, cid, tid, rel_type="亲属", label="父亲之死的知情人",
              description="详细说明全文"):
    return CharacterRelation(
        id=rid, project_id=uuid.uuid4(), character_id=cid, target_id=tid,
        rel_type=rel_type, label=label, description=description,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def _owner():
    return SimpleNamespace(id=uuid.uuid4())


def _char_row(cid, name):
    """select(Character.id, Character.name) 的行就是二元组——dict(rows) 直接吃。"""
    return (cid, name)


class TestListRelationsBrowse:
    async def _run(self, relation=None):
        pid = uuid.uuid4()
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        rel = relation or _relation(uuid.uuid4(), c1, c2, description="相识于少年")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[_owner()]),                                   # verify owner
            _Q(scalars=[rel]),                                        # all relations
            _Q(rows=[_char_row(c1, "林晓"), _char_row(c2, "苏菲")]),
        ])
        return await ListRelationsTool().run(db, project_id=str(pid), user_id="u1")

    async def test_browse_returns_light_rows(self):
        res = await self._run()
        assert res.success, res.error
        assert res.data["count"] == 1
        row = res.data["relations"][0]
        assert set(row) == {"id", "character_id", "character_name",
                            "target_id", "target_name", "rel_type", "label"}
        # browse level never carries the long description
        assert "description" not in row
        assert row["character_name"] == "林晓"
        assert row["target_name"] == "苏菲"

    async def test_no_strength_fields_anywhere(self):
        res = await self._run()
        row = res.data["relations"][0]
        for gone in ("trust", "threat", "attraction"):
            assert gone not in row, f"{gone} 是伪精度，已删除"
            assert not hasattr(CharacterRelation, gone)

    def test_no_filter_params(self):
        assert ListRelationsTool.meta.parameters["properties"] == {}

    def test_is_read_only(self):
        assert not ListRelationsTool().is_write_operation


class TestReadRelation:
    async def _run(self, rel=None):
        pid = uuid.uuid4()
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        rel = rel or _relation(uuid.uuid4(), c1, c2,
                               description="这条关系完整的背景说明，要写互动戏需要它")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[rel]),                                       # select relation
            _Q(scalars=[_owner()]),                                  # verify owner
            _Q(rows=[_char_row(c1, "林晓"), _char_row(c2, "苏菲")]),
        ])
        return await ReadRelationTool().run(
            db, project_id=str(pid), user_id="u1", relation_id=str(rel.id))

    async def test_single_relation_full_row(self):
        res = await self._run()
        assert res.success, res.error
        assert res.data["id"]
        assert res.data["description"] == "这条关系完整的背景说明，要写互动戏需要它"
        assert res.data["character_name"] == "林晓"
        assert res.data["target_name"] == "苏菲"
        assert res.data["rel_type"] == "亲属"
        assert res.data["label"] == "父亲之死的知情人"

    async def test_missing_relation_errors(self):
        pid = uuid.uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_Q(scalars=[])])
        res = await ReadRelationTool().run(
            db, project_id=str(pid), user_id="u1", relation_id=str(uuid.uuid4()))
        assert not res.success
        assert "关系" in res.error

    async def test_missing_relation_id_errors(self):
        res = await ReadRelationTool().run(AsyncMock(), user_id="u1")
        assert not res.success
        assert "relation_id" in res.error

    def test_requires_relation_id(self):
        assert ReadRelationTool.meta.parameters["required"] == ["relation_id"]


class TestSplitReadSurface:
    def test_relation_tools_are_registered_and_the_old_split_is_gone(self):
        from app.agent.tools import get_tool_registry

        reg = get_tool_registry()
        assert "list_relations" in reg
        assert "read_relation" in reg
        assert "list_character_relations" not in reg
