"""Ordering rules — ``sort_order`` is the single source of truth.

Chapters are numbered per act and scenes per chapter; new rows are appended
inside their own container, and the editor sync path owns the position so two
clients cannot invent the same number.
"""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.project.repository import ProjectRepository
from app.storycad.repository import AiStoryCADRepository


pytestmark = pytest.mark.asyncio


async def _project(db_session: AsyncSession, test_user: dict, acts: int = 2):
    repo = AiStoryCADRepository(db_session)
    project = await ProjectRepository(db_session).create("Ordering", "", test_user["id"])
    act_ids = [str(uuid.uuid4()) for _ in range(acts)]
    await repo.sync_editor_data(project.id, {"acts": {"created": [
        {"id": act_id, "name": f"第 {i + 1} 幕", "sort_order": i + 1}
        for i, act_id in enumerate(act_ids)
    ]}})
    return project, repo, act_ids


async def _add_chapter(repo: AiStoryCADRepository, project_id, act_id: str, title: str, **extra):
    chapter_id = str(uuid.uuid4())
    await repo.sync_editor_data(project_id, {"chapters": {"created": [
        {"id": chapter_id, "act_id": act_id, "title": title, **extra}
    ]}})
    return chapter_id


async def test_new_chapter_appends_within_its_own_act(db_session: AsyncSession, test_user: dict):
    """Chapter numbering is per act, so act 2 does not continue act 1's count."""
    project, repo, (act1, act2) = await _project(db_session, test_user)
    for title in ("A1", "A2", "A3"):
        await _add_chapter(repo, project.id, act1, title)
    await _add_chapter(repo, project.id, act2, "B1")

    data = await repo.get_editor_data(project.id)
    assert {c["title"]: c["sort_order"] for c in data["chapters"]} == {
        "A1": 1, "A2": 2, "A3": 3, "B1": 1,
    }


async def test_client_supplied_sort_order_is_ignored_on_create(db_session: AsyncSession, test_user: dict):
    """A client cannot inject a position: the server appends inside the act."""
    project, repo, (act1,) = await _project(db_session, test_user, acts=1)
    await repo.sync_editor_data(project.id, {"chapters": {"created": [
        {"id": str(uuid.uuid4()), "act_id": act1, "title": "first", "sort_order": 7},
        {"id": str(uuid.uuid4()), "act_id": act1, "title": "second", "sort_order": 7},
    ]}})

    data = await repo.get_editor_data(project.id)
    assert [c["sort_order"] for c in data["chapters"]] == [1, 2]


async def test_new_scene_appends_within_its_chapter(db_session: AsyncSession, test_user: dict):
    """Scenes are numbered per chapter; two chapters never share the count."""
    project, repo, (act1,) = await _project(db_session, test_user, acts=1)
    ch1 = await _add_chapter(repo, project.id, act1, "C1")
    ch2 = await _add_chapter(repo, project.id, act1, "C2")
    for chapter_id, title in ((ch1, "s1"), (ch1, "s2"), (ch2, "s3")):
        await repo.sync_editor_data(project.id, {"scenes": {"created": [
            {"id": str(uuid.uuid4()), "chapter_id": chapter_id, "title": title}
        ]}})

    data = await repo.get_editor_data(project.id)
    assert {s["title"]: s["sort_order"] for s in data["scenes"]} == {
        "s1": 1, "s2": 2, "s3": 1,
    }


async def test_editor_data_returns_chapters_in_narrative_order(db_session: AsyncSession, test_user: dict):
    """Payload order is act order then in-act order, not creation order."""
    project, repo, (act1, act2) = await _project(db_session, test_user)
    await _add_chapter(repo, project.id, act1, "A1")
    await _add_chapter(repo, project.id, act2, "B1")
    await _add_chapter(repo, project.id, act1, "A2")

    data = await repo.get_editor_data(project.id)
    assert [c["title"] for c in data["chapters"]] == ["A1", "A2", "B1"]


async def test_update_chapter_accepts_sort_order(db_session: AsyncSession, test_user: dict):
    """update_chapter is the single-row way to move a chapter inside its act."""
    from app.agent.tools.project_tools import UpdateChapterTool

    project, repo, (act1,) = await _project(db_session, test_user, acts=1)
    first = await _add_chapter(repo, project.id, act1, "first")
    second = await _add_chapter(repo, project.id, act1, "second")

    tool = UpdateChapterTool()
    result = await tool.run(
        db=db_session, project_id=str(project.id), user_id=str(test_user["id"]),
        chapter_id=second, sort_order=1,
    )
    assert result.success, result.error
    assert result.data["sort_order"] == 1
    assert first


async def test_move_chapter_places_chapter_after_target(db_session: AsyncSession, test_user: dict):
    """move_chapter renumbers 1..N so the new position is unambiguous."""
    from app.agent.tools.project_admin_tools import MoveChapterTool

    project, repo, (act1,) = await _project(db_session, test_user, acts=1)
    a = await _add_chapter(repo, project.id, act1, "A")
    b = await _add_chapter(repo, project.id, act1, "B")
    c = await _add_chapter(repo, project.id, act1, "C")

    tool = MoveChapterTool()
    result = await tool.run(
        db=db_session, project_id=str(project.id), user_id=str(test_user["id"]),
        chapter_id=c, after_chapter_id=a,
    )
    assert result.success, result.error
    assert [(row["title"], row["sort_order"]) for row in result.data["order"]] == [
        ("A", 1), ("C", 2), ("B", 3),
    ]
    assert b


async def test_move_chapter_rejects_other_act_target(db_session: AsyncSession, test_user: dict):
    """A chapter may only move inside its own act."""
    from app.agent.tools.project_admin_tools import MoveChapterTool

    project, repo, (act1, act2) = await _project(db_session, test_user)
    a = await _add_chapter(repo, project.id, act1, "A")
    other = await _add_chapter(repo, project.id, act2, "B")

    tool = MoveChapterTool()
    result = await tool.run(
        db=db_session, project_id=str(project.id), user_id=str(test_user["id"]),
        chapter_id=a, after_chapter_id=other,
    )
    assert not result.success
    assert "同一幕" in (result.error or "")