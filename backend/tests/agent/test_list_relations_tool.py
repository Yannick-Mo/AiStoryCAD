"""Tests for ListRelationsTool's read modes (browse / character / single /
type) and the resulting data shape."""
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.tools.list_tools import ListRelationsTool
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


def _relation(rid, cid, tid, rel_type="好友", label="", description="详情", trust=80, threat=10, attraction=50):
    return CharacterRelation(
        id=rid, project_id=uuid.uuid4(), character_id=cid, target_id=tid,
        rel_type=rel_type, label=label, description=description,
        trust=trust, threat=threat, attraction=attraction,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def _make_db(relations, char_pairs):
    """db.execute call order: verify (project) -> relations -> character names."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),
        _Q(scalars=relations),
        _Q(rows=char_pairs),
    ])
    return db


class TestMeta:
    def test_params_declared(self):
        props = ListRelationsTool.meta.parameters["properties"]
        assert {"character_id", "relation_id", "rel_type"} <= set(props)

    def test_description_explains_modes(self):
        desc = ListRelationsTool.meta.description
        assert "character_id" in desc and "relation_id" in desc and "rel_type" in desc
        assert "精读" in desc
        assert "数值" in desc


class TestRunModes:
    def _setup(self):
        pid = uuid.uuid4()
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        rels = [
            _relation(uuid.uuid4(), c1, c2, rel_type="好友", label="挚友", description="相识于少年时代，彼此信任", trust=90, threat=5, attraction=40),
            _relation(uuid.uuid4(), c1, uuid.uuid4(), rel_type="敌对", label="死敌", description="争夺王位", trust=10, threat=95, attraction=10),
        ]
        char_names = [
            SimpleNamespace(id=c1, name="林晓"),
            SimpleNamespace(id=c2, name="苏菲"),
        ]
        db = _make_db(rels, char_names)
        tool = ListRelationsTool()
        return db, tool, pid

    async def _run(self, **params):
        db, tool, pid = self._setup()
        return await tool.run(db, project_id=str(pid), user_id="u1", **params)

    async def test_browse_all(self):
        res = await self._run()
        assert res.success
        assert res.data["total"] == 2
        assert len(res.data["relations"]) == 2

    async def test_character_filter_ok(self):
        res = await self._run(character_id=str(uuid.uuid4()))
        assert res.success
        assert "relations" in res.data

    async def test_rel_type_filter_ok(self):
        res = await self._run(rel_type="敌对")
        assert res.success

    async def test_relation_id_single_ok(self):
        rid = uuid.uuid4()
        res = await self._run(relation_id=str(rid))
        assert res.success
        assert res.data["total"] == 2  # fake db returns the same rows regardless

    async def test_relation_rows_have_full_fields(self):
        res = await self._run()
        row = res.data["relations"][0]
        for key in ("id", "character_name", "target_name", "rel_type",
                    "description", "trust", "threat", "attraction"):
            assert key in row, f"missing field {key}"


class TestValidation:
    async def test_bad_relation_id_returns_error(self):
        db = _make_db([], [])
        tool = ListRelationsTool()
        res = await tool.run(db, project_id=str(uuid.uuid4()), user_id="u1",
                             relation_id="not-a-uuid")
        assert not res.success
