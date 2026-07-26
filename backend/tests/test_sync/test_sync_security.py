"""Tests that sync operations scope entity lookups to the owning project,
and that create_entity cannot be abused to inject server-managed fields."""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.storycad.repository import StoryCADRepository, CREATE_PROTECTED
from app.project.repository import ProjectRepository
from app.storycad.models import Act, Chapter, Scene, SceneContent


pytestmark = pytest.mark.asyncio


async def _create_own_project(
    db_session: AsyncSession, test_user: dict,
) -> tuple[StoryCADRepository, object, str, str, str]:
    """Create a project with one act/chapter/scene; return (repo, project, act_id, chapter_id, scene_id)."""
    repo = StoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)
    project = await project_repo.create("OWN Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())
    scene_id = str(uuid.uuid4())
    payload = {
        "acts": {"created": [{"id": act_id, "name": "OWN Act", "sort_order": 0}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "OWN Chapter", "sort_order": 0}]},
        "scenes": {"created": [{"id": scene_id, "chapter_id": chapter_id, "title": "OWN Scene", "sort_order": 0}]},
    }
    await repo.sync_editor_data(project.id, payload)
    return repo, project, act_id, chapter_id, scene_id


async def _create_foreign_project(
    db_session: AsyncSession, test_user: dict,
) -> tuple[StoryCADRepository, object, str, str, str]:
    """Create a second project with one act/chapter/scene."""
    repo = StoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)
    project = await project_repo.create("FOREIGN Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())
    scene_id = str(uuid.uuid4())
    payload = {
        "acts": {"created": [{"id": act_id, "name": "FOREIGN Act", "sort_order": 0}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "FOREIGN Chapter", "sort_order": 0}]},
        "scenes": {"created": [{"id": scene_id, "chapter_id": chapter_id, "title": "FOREIGN Scene", "sort_order": 0}]},
    }
    await repo.sync_editor_data(project.id, payload)
    return repo, project, act_id, chapter_id, scene_id


async def _create_simple_project(
    db_session: AsyncSession, test_user: dict,
) -> tuple[StoryCADRepository, object, str]:
    """Minimal project with one act (no chapter/scene) for field-filter tests."""
    repo = StoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)
    project = await project_repo.create("Test Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    payload = {
        "acts": {"created": [{"id": act_id, "name": "Test Act", "sort_order": 0}]},
    }
    await repo.sync_editor_data(project.id, payload)
    return repo, project, act_id


# =========================================================================
# Fix A: sync_editor_data scopes update/delete to the owning project
# =========================================================================


async def test_sync_update_skips_entity_from_other_project(
    db_session: AsyncSession, test_user: dict,
):
    """Updating an entity that belongs to a different project must be silently skipped."""
    repo_own, own_project, _, own_chapter_id, _ = await _create_own_project(
        db_session, test_user,
    )
    _, foreign_project, _, _, _ = await _create_foreign_project(
        db_session, test_user,
    )

    # Attempt to modify the OWN project's chapter via a sync on the FOREIGN project
    payload = {
        "chapters": {
            "updated": [{"id": own_chapter_id, "title": "HACKED"}],
        },
    }
    await repo_own.sync_editor_data(foreign_project.id, payload)

    # Verify OWN chapter was NOT modified
    fetched_own = await repo_own.get_entity(Chapter, uuid.UUID(own_chapter_id))
    assert fetched_own["title"] == "OWN Chapter", "Entity from other project must not be updated"


async def test_sync_update_skips_nonexistent_entity(
    db_session: AsyncSession, test_user: dict,
):
    """Updating a non-existent entity id must be silently skipped (no crash)."""
    repo_own, own_project, _, _, _ = await _create_own_project(
        db_session, test_user,
    )
    fake_id = str(uuid.uuid4())

    payload = {
        "chapters": {
            "updated": [{"id": fake_id, "title": "NOWHERE"}],
        },
    }
    # Must not raise; must not modify anything
    await repo_own.sync_editor_data(own_project.id, payload)


async def test_sync_delete_skips_entity_from_other_project(
    db_session: AsyncSession, test_user: dict,
):
    """Deleting an entity that belongs to a different project must be silently skipped."""
    repo_own, own_project, _, own_chapter_id, _ = await _create_own_project(
        db_session, test_user,
    )
    _, foreign_project, _, _, _ = await _create_foreign_project(
        db_session, test_user,
    )

    # Attempt to delete OWN chapter via FOREIGN project sync
    payload = {
        "chapters": {
            "deleted": [own_chapter_id],
        },
    }
    await repo_own.sync_editor_data(foreign_project.id, payload)

    # Verify OWN chapter still exists
    fetched = await repo_own.get_entity(Chapter, uuid.UUID(own_chapter_id))
    assert fetched is not None, "Entity from other project must not be deleted"


async def test_sync_delete_skips_nonexistent_entity(
    db_session: AsyncSession, test_user: dict,
):
    """Deleting a non-existent entity id must be silently skipped (no crash)."""
    repo_own, own_project, _, _, _ = await _create_own_project(
        db_session, test_user,
    )
    fake_id = str(uuid.uuid4())

    payload = {
        "acts": {
            "deleted": [fake_id],
        },
    }
    await repo_own.sync_editor_data(own_project.id, payload)


async def test_sync_update_within_own_project_still_works(
    db_session: AsyncSession, test_user: dict,
):
    """Updating an entity within the same project must still succeed normally."""
    repo_own, own_project, _, own_chapter_id, _ = await _create_own_project(
        db_session, test_user,
    )

    payload = {
        "chapters": {
            "updated": [{"id": own_chapter_id, "title": "Updated Chapter"}],
        },
    }
    version = await repo_own.sync_editor_data(own_project.id, payload)
    assert version >= 1, "sync within own project must succeed"

    fetched = await repo_own.get_entity(Chapter, uuid.UUID(own_chapter_id))
    assert fetched["title"] == "Updated Chapter"


async def test_sync_delete_within_own_project_still_works(
    db_session: AsyncSession, test_user: dict,
):
    """Deleting an entity within the same project must still succeed normally."""
    repo_own, own_project, _, own_chapter_id, _ = await _create_own_project(
        db_session, test_user,
    )

    payload = {
        "chapters": {
            "deleted": [own_chapter_id],
        },
    }
    version = await repo_own.sync_editor_data(own_project.id, payload)
    assert version >= 1, "sync within own project must succeed"

    fetched = await repo_own.get_entity(Chapter, uuid.UUID(own_chapter_id))
    assert fetched is None, "Entity must be deleted"


# =========================================================================
# Fix B: create_entity filters protected fields
# =========================================================================


async def test_create_entity_ignores_word_count(
    db_session: AsyncSession, test_user: dict,
):
    """word_count must be stripped from create_entity data."""
    repo, project, act_id = await _create_simple_project(db_session, test_user)
    chapter_id = str(uuid.uuid4())
    # Create a real chapter first (needed for scene FK)
    await repo.sync_editor_data(project.id, {
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "C", "sort_order": 0}]},
    })

    scene = await repo.create_entity(Scene, {
        "id": str(uuid.uuid4()),
        "project_id": str(project.id),
        "chapter_id": chapter_id,
        "title": "Test Scene",
        "sort_order": 0,
        "word_count": 99999,
    })
    assert scene["word_count"] == 0, "word_count must be ignored on create"


async def test_create_entity_ignores_created_at(
    db_session: AsyncSession, test_user: dict,
):
    """created_at must be stripped from create_entity data."""
    repo, project, _ = await _create_simple_project(db_session, test_user)

    forged = "2000-01-01T00:00:00+00:00"
    act = await repo.create_entity(Act, {
        "id": str(uuid.uuid4()),
        "project_id": str(project.id),
        "name": "Test Act",
        "sort_order": 0,
        "created_at": forged,
    })
    # created_at should be set server-side, not the forged value
    assert act["created_at"] != forged, "created_at must not be client-settable"


async def test_create_entity_ignores_scene_count_and_total_words(
    db_session: AsyncSession, test_user: dict,
):
    """Server-computed chapter statistics must be ignored on create."""
    repo, project, act_id = await _create_simple_project(db_session, test_user)

    chapter = await repo.create_entity(Chapter, {
        "id": str(uuid.uuid4()),
        "project_id": str(project.id),
        "act_id": act_id,
        "title": "Test Chapter",
        "sort_order": 0,
        "scene_count": 42,
        "total_words": 99999,
    })
    assert chapter["scene_count"] == 0, "scene_count must be ignored on create"
    assert chapter["total_words"] == 0, "total_words must be ignored on create"


async def test_create_entity_allows_id_and_project_id(
    db_session: AsyncSession, test_user: dict,
):
    """id and project_id are legitimate at creation time even though they are protected on update."""
    repo, project, _ = await _create_simple_project(db_session, test_user)

    expected_id = str(uuid.uuid4())
    act = await repo.create_entity(Act, {
        "id": expected_id,
        "project_id": str(project.id),
        "name": "Legitimate Act",
        "sort_order": 0,
    })
    assert act["id"] == expected_id, "id must be settable on create"
    assert act["project_id"] == str(project.id), "project_id must be settable on create"


async def test_create_protected_constant_definition():
    """Verifies CREATE_PROTECTED excludes id and project_id from PROTECTED_FIELDS."""
    assert "id" not in CREATE_PROTECTED
    assert "project_id" not in CREATE_PROTECTED
    assert "word_count" in CREATE_PROTECTED
    assert "created_at" in CREATE_PROTECTED
    assert "scene_count" in CREATE_PROTECTED
    assert "total_words" in CREATE_PROTECTED
