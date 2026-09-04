"""Tests for the split relation/scene navigation tools:
list_relations (browse), list_character_relations (per-character network),
read_relation (single full row), read_chapter_scenes (chapter scene list)."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.tools.list_tools import (
    ListRelationsTool,
    ListCharacterRelationsTool,
    ReadRelationTool,
    ReadChapterScenesTool,
)
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


def _relation(rid, cid, tid, rel_type="好友", label="挚友", description="详细说明全文", trust=80, threat=10, attraction=50):
    return CharacterRelation(
        id=rid, project_id=uuid.uuid4(), character_id=cid, target_id=tid,
        rel_type=rel_type, label=label, description=description,
        trust=trust, threat=threat, attraction=attraction,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def _char_row(cid, name):
    return SimpleNamespace(id=cid, name=name)


class TestListRelationsBrowse:
    async def _run(self):
        pid = uuid.uuid4()
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        rel = _relation(uuid.uuid4(), c1, c2, description="相识于少年")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),   # verify owner
            _Q(scalars=[rel]),                                 # all relations
            _Q(rows=[_char_row(c1, "林晓"), _char_row(c2, "苏菲")]),
        ])
        tool = ListRelationsTool()
        return await tool.run(db, project_id=str(pid), user_id="u1")

    async def test_browse_returns_light_rows(self):
        res = await self._run()
        assert res.success
        assert res.data["total"] == 1
        row = res.data["relations"][0]
        for key in ("id", "character_name", "target_name", "rel_type",
                    "label", "trust", "threat", "attraction"):
            assert key in row, f"missing {key}"
        # browse level never carries the long description
        assert "description" not in row
        assert row["character_name"] == "林晓"

    def test_no_filter_params(self):
        assert ListRelationsTool.meta.parameters["properties"] == {}


class TestListCharacterRelations:
    async def test_per_character_rows(self):
        pid = uuid.uuid4()
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        rel = _relation(uuid.uuid4(), c1, c2)
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),
            _Q(scalars=[rel]),
            _Q(rows=[_char_row(c1, "林晓"), _char_row(c2, "苏菲")]),
        ])
        tool = ListCharacterRelationsTool()
        res = await tool.run(db, project_id=str(pid), user_id="u1",
                             character_id=str(c1))
        assert res.success
        assert res.data["total"] == 1
        assert "description" not in res.data["relations"][0]

    def test_requires_character_id(self):
        assert "character_id" in ListCharacterRelationsTool.meta.parameters["required"]


class TestReadRelation:
    async def _run(self):
        pid = uuid.uuid4()
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        rel = _relation(uuid.uuid4(), c1, c2, description="这条关系完整的背景说明，要写互动戏需要它")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),
            _Q(scalars=[rel]),
            _Q(rows=[_char_row(c1, "林晓"), _char_row(c2, "苏菲")]),
        ])
        tool = ReadRelationTool()
        return await tool.run(db, project_id=str(pid), user_id="u1",
                              relation_id=str(rel.id))

    async def test_single_relation_full_row(self):
        res = await self._run()
        assert res.success
        assert res.data["total"] == 1
        row = res.data["relations"][0]
        assert row["description"] == "这条关系完整的背景说明，要写互动戏需要它"
        assert row["character_name"] == "林晓"

    async def test_missing_relation_errors(self):
        pid = uuid.uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),
            _Q(scalars=[]),
        ])
        tool = ReadRelationTool()
        res = await tool.run(db, project_id=str(pid), user_id="u1",
                             relation_id=str(uuid.uuid4()))
        assert not res.success


class TestReadChapterScenes:
    async def _run(self, scenes=None):
        pid, cid = uuid.uuid4(), uuid.uuid4()
        chapter = SimpleNamespace(id=cid, project_id=pid, title="第一章")
        scenes = scenes or [
            SimpleNamespace(id=uuid.uuid4(), title="开场", sort_order=1,
                            pov_character="林晓", word_count=0),
            SimpleNamespace(id=uuid.uuid4(), title="冲突", sort_order=2,
                            pov_character="苏菲", word_count=3200),
        ]
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[chapter]),
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),
            _Q(scalars=scenes),
        ])
        tool = ReadChapterScenesTool()
        return await tool.run(db, project_id=str(pid), user_id="u1",
                              chapter_id=str(cid))

    async def test_light_navigation_rows(self):
        res = await self._run()
        assert res.success
        assert res.data["chapter_title"] == "第一章"
        assert res.data["total"] == 2
        row = res.data["scenes"][0]
        for key in ("id", "title", "sort_order", "pov_character",
                    "word_count", "written"):
            assert key in row
        # navigation rows never carry the blueprint
        assert "summary" not in row
        assert res.data["scenes"][1]["written"] is True
        assert res.data["scenes"][0]["written"] is False

    async def test_unknown_chapter_errors(self):
        pid = uuid.uuid4()
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[]),
        ])
        tool = ReadChapterScenesTool()
        res = await tool.run(db, project_id=str(pid), user_id="u1",
                             chapter_id=str(uuid.uuid4()))
        assert not res.success
