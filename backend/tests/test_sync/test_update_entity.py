"""Tests that update_entity cannot tamper with server-managed / protected fields."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.storycad.repository import StoryCADRepository
from app.project.repository import ProjectRepository
from app.storycad.models import Chapter, Scene


pytestmark = pytest.mark.asyncio


async def _setup(db_session: AsyncSession, test_user: dict):
    """Create a project with one act/chapter/scene; return (repo, project, chapter_id, scene_id)."""
    repo = StoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)
    project = await project_repo.create("Test Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())
    scene_id = str(uuid.uuid4())
    payload = {
        "acts": {"created": [{"id": act_id, "name": "第一幕", "sort_order": 0}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "第一章", "sort_order": 0}]},
        "scenes": {"created": [{"id": scene_id, "chapter_id": chapter_id, "title": "场景 1", "sort_order": 0}]},
    }
    await repo.sync_editor_data(project.id, payload)
    return repo, project, chapter_id, scene_id


async def test_update_entity_cannot_change_project_id(db_session: AsyncSession, test_user: dict):
    """Passing a foreign project_id must be ignored while other fields still update."""
    repo, project, chapter_id, _ = await _setup(db_session, test_user)
    foreign_project_id = str(uuid.uuid4())

    result = await repo.update_entity(Chapter, {
        "id": chapter_id,
        "title": "新标题",
        "project_id": foreign_project_id,
    })

    assert result is not None
    assert result["title"] == "新标题"
    assert result["project_id"] == str(project.id), "project_id must not be reassignable via update"

    fetched = await repo.get_entity(Chapter, uuid.UUID(chapter_id))
    assert fetched["project_id"] == str(project.id)


async def test_update_entity_cannot_change_id(db_session: AsyncSession, test_user: dict):
    """The entity id must remain stable; it is only a lookup key, never writable."""
    repo, _, chapter_id, _ = await _setup(db_session, test_user)

    result = await repo.update_entity(Chapter, {"id": chapter_id, "title": "改名"})

    assert result is not None
    assert result["id"] == chapter_id
    fetched = await repo.get_entity(Chapter, uuid.UUID(chapter_id))
    assert fetched is not None
    assert fetched["id"] == chapter_id


async def test_update_entity_cannot_overwrite_server_computed_fields(db_session: AsyncSession, test_user: dict):
    """Server-computed statistics (word_count / scene_count / total_words) must be ignored."""
    repo, _, chapter_id, scene_id = await _setup(db_session, test_user)

    scene_result = await repo.update_entity(Scene, {
        "id": scene_id,
        "summary": "新摘要",
        "word_count": 99999,
    })
    assert scene_result is not None
    assert scene_result["summary"] == "新摘要"
    assert scene_result["word_count"] == 0, "word_count is server-computed and must not be client-writable"

    chapter_result = await repo.update_entity(Chapter, {
        "id": chapter_id,
        "goal": "新目标",
        "scene_count": 42,
        "total_words": 123456,
    })
    assert chapter_result is not None
    assert chapter_result["goal"] == "新目标"
    assert chapter_result["scene_count"] == 1, "scene_count is server-computed and must not be client-writable"
    assert chapter_result["total_words"] == 0, "total_words is server-computed and must not be client-writable"


async def test_update_entity_ignores_timestamp_fields(db_session: AsyncSession, test_user: dict):
    """created_at is server-managed and must not be overwritten by the client."""
    repo, _, chapter_id, _ = await _setup(db_session, test_user)
    original = await repo.get_entity(Chapter, uuid.UUID(chapter_id))
    forged = datetime(2000, 1, 1, tzinfo=timezone.utc)

    result = await repo.update_entity(Chapter, {
        "id": chapter_id,
        "title": "时间戳测试",
        "created_at": forged,
    })

    assert result is not None
    assert result["title"] == "时间戳测试"
    assert result["created_at"] == original["created_at"]
