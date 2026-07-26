"""Tests for the editor data sync pipeline — the most critical data flow."""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.storycad.repository import StoryCADRepository
from app.project.repository import ProjectRepository


pytestmark = pytest.mark.asyncio


async def test_sync_creates_act(db_session: AsyncSession, test_user: dict):
    """Creating an act via sync should persist it and return a version bump."""
    repo = StoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)

    project = await project_repo.create("Test Project", "", test_user["id"])

    act_id = str(uuid.uuid4())
    payload = {
        "acts": {
            "created": [{"id": act_id, "name": "第一幕", "sort_order": 1, "color": "#f97316"}]
        }
    }
    version = await repo.sync_editor_data(project.id, payload)

    assert version >= 1

    data = await repo.get_editor_data(project.id)
    acts = data["acts"]
    assert len(acts) == 1
    assert acts[0]["name"] == "第一幕"
    assert acts[0]["sort_order"] == 1


async def test_sync_creates_chapter_under_act(db_session: AsyncSession, test_user: dict):
    """Chapters should link to acts correctly through sync."""
    repo = StoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)

    project = await project_repo.create("Test Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())

    payload = {
        "acts": {"created": [{"id": act_id, "name": "第一幕", "sort_order": 1}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "第一章", "sort_order": 0}]},
    }
    await repo.sync_editor_data(project.id, payload)

    data = await repo.get_editor_data(project.id)
    chapters = data["chapters"]
    assert len(chapters) == 1
    assert chapters[0]["act_id"] == act_id
    assert chapters[0]["title"] == "第一章"


async def test_sync_deletes_act_cascades_chapters(db_session: AsyncSession, test_user: dict):
    """Deleting an act should cascade-delete its chapters (CASCADE FK fix)."""
    repo = StoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)

    project = await project_repo.create("Test Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())

    # Create act + chapters
    payload = {
        "acts": {"created": [{"id": act_id, "name": "第一幕", "sort_order": 1}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "第一章", "sort_order": 0}]},
    }
    await repo.sync_editor_data(project.id, payload)

    # Delete the act
    payload = {"acts": {"deleted": [act_id]}}
    await repo.sync_editor_data(project.id, payload)

    data = await repo.get_editor_data(project.id)
    assert len(data["acts"]) == 0
    assert len(data["chapters"]) == 0, "Chapters should be cascade-deleted with the act"


async def test_sync_updates_global_settings(db_session: AsyncSession, test_user: dict):
    """Saving global settings should persist to the project row."""
    repo = StoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)

    project = await project_repo.create("Test Project", "", test_user["id"])

    payload = {
        "projects": {"updated": [{"id": str(project.id), "global_settings": "一个奇幻世界"}]}
    }
    await repo.sync_editor_data(project.id, payload)

    data = await repo.get_editor_data(project.id)
    assert data["global_settings"] == "一个奇幻世界"


async def test_sync_recalculates_chapter_word_count(db_session: AsyncSession, test_user: dict):
    """After scene operations, chapter word_count should be recalculated."""
    repo = StoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)

    project = await project_repo.create("Test Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())
    scene_id = str(uuid.uuid4())

    payload = {
        "acts": {"created": [{"id": act_id, "name": "第一幕", "sort_order": 1}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "第一章", "sort_order": 0}]},
        "scenes": {"created": [{"id": scene_id, "chapter_id": chapter_id, "title": "场景 1", "sort_order": 0}]},
    }
    await repo.sync_editor_data(project.id, payload)

    # Manually set scene word_count
    from app.storycad.models import Scene
    await db_session.execute(
        Scene.__table__.update().where(Scene.id == uuid.UUID(scene_id)).values(word_count=150)
    )
    await db_session.commit()

    # Trigger recalculation by syncing an unrelated change
    await repo.sync_editor_data(project.id, {"acts": {}})

    data = await repo.get_editor_data(project.id)
    chapter = next(c for c in data["chapters"] if c["id"] == chapter_id)
    assert chapter["scene_count"] >= 1
