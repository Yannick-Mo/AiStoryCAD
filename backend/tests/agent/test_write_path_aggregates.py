"""Batch C tests:
1. Body-write tools refresh the chapter aggregates (scene_count/total_words).
2. create_scene / update_scene return fresh word_count + content_preview.
3. base._err translates UUID / permission failures into teaching Chinese.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent.tools.base import BaseTool, ToolResult
from app.agent.tools.project_tools import (
    CreateSceneTool, UpdateSceneTool,
)
from app.agent.tools.writing_tools import WriteSceneContentTool


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


class TestChapterAggregateRefresh:
    async def test_write_scene_content_recalcs_chapter(self):
        pid = uuid.uuid4()
        ch_id = uuid.uuid4()
        sc_id = uuid.uuid4()
        scene = SimpleNamespace(id=sc_id, project_id=pid, chapter_id=ch_id,
                                word_count=0)
        content = SimpleNamespace(scene_id=sc_id, content="正文内容")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[scene]),
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),
            _Q(scalars=[content]),
        ])
        db.commit = AsyncMock()
        with patch("app.agent.tools.writing_tools.AiStoryCADRepository") as repo_cls:
            repo = repo_cls.return_value
            repo.recalc_chapter = AsyncMock()
            res = await WriteSceneContentTool().run(
                db, project_id=str(pid), user_id="u", scene_id=str(sc_id),
                content="新正文内容")
        assert res.success
        assert res.data["word_count"] > 0
        assert res.data["content_preview"] == "新正文内容"
        repo.recalc_chapter.assert_awaited_once_with(ch_id)

    async def test_update_scene_content_refreshes_returned_counts(self):
        pid = uuid.uuid4()
        ch_id = uuid.uuid4()
        sc_id = uuid.uuid4()
        scene = SimpleNamespace(id=sc_id, project_id=pid, chapter_id=ch_id,
                                title="场", summary="", word_count=0)
        content = SimpleNamespace(scene_id=sc_id, content="旧文")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),  # verify owner
            _Q(scalars=[content]),                           # select SceneContent
        ])
        db.get = AsyncMock(return_value=scene)
        with patch("app.agent.tools.project_tools.AiStoryCADRepository") as repo_cls:
            repo = repo_cls.return_value
            repo.update_entity = AsyncMock(return_value={"id": str(sc_id)})
            repo.recalc_chapter = AsyncMock()
            res = await UpdateSceneTool().run(
                db, project_id=str(pid), user_id="u", scene_id=str(sc_id),
                content="完整新正文内容")
        assert res.success
        assert res.data["word_count"] > 0
        assert "content_preview" in res.data
        repo.recalc_chapter.assert_awaited_once_with(ch_id)

    async def test_create_scene_with_content_returns_word_count(self):
        pid = uuid.uuid4()
        ch_id = uuid.uuid4()
        chapter = SimpleNamespace(id=ch_id, project_id=pid)
        scene = SimpleNamespace(id=uuid.uuid4(), project_id=pid,
                                chapter_id=ch_id, word_count=0)
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),  # verify owner
        ])
        db.get = AsyncMock(side_effect=[chapter, scene])     # Chapter then Scene
        db.add = AsyncMock()
        with patch("app.agent.tools.project_tools.AiStoryCADRepository") as repo_cls:
            repo = repo_cls.return_value
            repo.create_entity = AsyncMock(return_value={"id": str(scene.id)})
            repo.recalc_chapter = AsyncMock()
            res = await CreateSceneTool().run(
                db, project_id=str(pid), user_id="u", chapter_id=str(ch_id),
                title="新场", content="一段新正文")
        assert res.success
        assert res.data["word_count"] > 0
        assert "content_preview" in res.data
        repo.recalc_chapter.assert_awaited_once_with(ch_id)


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
