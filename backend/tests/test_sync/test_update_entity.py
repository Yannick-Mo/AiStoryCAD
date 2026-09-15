"""Tests that update_entity cannot tamper with server-managed / protected fields.

派生统计列（chapter.word_count / scene_count / total_words）已随重构删除：
字数实时算，所以它们不再是"受保护字段"——而是**根本不是列**，传了自然被忽略。
"""
import uuid
from datetime import datetime, UTC

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.storycad.repository import AiStoryCADRepository
from app.project.repository import ProjectRepository
from app.storycad.models import Chapter


pytestmark = pytest.mark.asyncio


async def _setup(db_session: AsyncSession, test_user: dict):
    """Create a project with one act/chapter; return (repo, project, chapter_id)."""
    repo = AiStoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)
    project = await project_repo.create("Test Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())
    await repo.sync_editor_data(project.id, {
        "acts": {"created": [{"id": act_id, "name": "第一幕", "purpose": "甲"}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id,
                                  "title": "第一章", "goal": "原目标"}]},
    })
    return repo, project, chapter_id


async def test_update_entity_cannot_change_project_id(db_session: AsyncSession, test_user: dict):
    """Passing a foreign project_id must be ignored while other fields still update."""
    repo, project, chapter_id = await _setup(db_session, test_user)
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
    repo, _, chapter_id = await _setup(db_session, test_user)

    result = await repo.update_entity(Chapter, {"id": chapter_id, "title": "改名"})

    assert result is not None
    assert result["id"] == chapter_id
    fetched = await repo.get_entity(Chapter, uuid.UUID(chapter_id))
    assert fetched is not None
    assert fetched["id"] == chapter_id


async def test_update_entity_ignores_fields_that_are_not_columns(db_session: AsyncSession, test_user: dict):
    """客户端塞一个不存在的派生字段进来：合法字段照常生效，多出来的被丢掉。"""
    repo, _, chapter_id = await _setup(db_session, test_user)

    result = await repo.update_entity(Chapter, {
        "id": chapter_id,
        "goal": "新目标",
        "word_count": 99999,
        "scene_count": 42,
        "total_words": 123456,
    })

    assert result is not None
    assert result["goal"] == "新目标"
    for gone in ("word_count", "scene_count", "total_words"):
        assert gone not in result, f"{gone} 不是列，不该出现在载荷里且不该被写入"

    fetched = await repo.get_entity(Chapter, uuid.UUID(chapter_id))
    assert fetched["goal"] == "新目标"
    assert set(fetched) == {c.name for c in Chapter.__table__.columns} - {"content"}


async def test_update_entity_ignores_timestamp_fields(db_session: AsyncSession, test_user: dict):
    """created_at is server-managed and must not be overwritten by the client."""
    repo, _, chapter_id = await _setup(db_session, test_user)
    original = await repo.get_entity(Chapter, uuid.UUID(chapter_id))
    forged = datetime(2000, 1, 1, tzinfo=UTC)

    result = await repo.update_entity(Chapter, {
        "id": chapter_id,
        "title": "时间戳测试",
        "created_at": forged,
    })

    assert result is not None
    assert result["title"] == "时间戳测试"
    assert result["created_at"] == original["created_at"]


async def test_update_entity_can_move_a_chapter_to_another_act(db_session: AsyncSession, test_user: dict):
    """归属是可以改的（规格第 5 节第 2 条）：act_id 不在保护名单里。"""
    repo, project, chapter_id = await _setup(db_session, test_user)
    other_act = str(uuid.uuid4())
    await repo.sync_editor_data(project.id, {
        "acts": {"created": [{"id": other_act, "name": "第二幕", "purpose": "乙"}]},
    })

    result = await repo.update_entity(Chapter, {"id": chapter_id, "act_id": other_act})

    assert result is not None
    assert result["act_id"] == other_act
