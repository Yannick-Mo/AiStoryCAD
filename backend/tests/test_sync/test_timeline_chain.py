"""The main timeline is a projection of the chapter order.

Every order change (create / delete / move a chapter, delete an act) rewrites
the timeline into one straight chain following the reading order.  Only
``timeline`` edges are touched and the projection is idempotent.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.project.repository import ProjectRepository
from app.storycad.models import ChapterEdge
from app.storycad.repository import AiStoryCADRepository
from app.storycad.timeline import reconcile_timeline_chain


pytestmark = pytest.mark.asyncio


async def _project(db_session: AsyncSession, test_user: dict, acts: int = 1):
    repo = AiStoryCADRepository(db_session)
    project = await ProjectRepository(db_session).create("Timeline", "", test_user["id"])
    act_ids = [str(uuid.uuid4()) for _ in range(acts)]
    await repo.sync_editor_data(project.id, {"acts": {"created": [
        {"id": act_id, "name": f"第 {i + 1} 幕", "sort_order": i + 1}
        for i, act_id in enumerate(act_ids)
    ]}})
    return project, repo, act_ids


async def _add_chapter(repo: AiStoryCADRepository, project_id, act_id: str, title: str) -> str:
    chapter_id = str(uuid.uuid4())
    await repo.sync_editor_data(project_id, {"chapters": {"created": [
        {"id": chapter_id, "act_id": act_id, "title": title}
    ]}})
    return chapter_id


async def _timeline_pairs(db_session: AsyncSession, project_id) -> set[tuple[str, str]]:
    rows = (await db_session.execute(
        select(ChapterEdge).where(
            ChapterEdge.project_id == project_id,
            ChapterEdge.edge_type == "timeline",
        )
    )).scalars().all()
    return {(str(e.source_id), str(e.target_id)) for e in rows}


async def test_new_chapters_are_chained_in_order(db_session: AsyncSession, test_user: dict):
    project, repo, (act1,) = await _project(db_session, test_user)
    a = await _add_chapter(repo, project.id, act1, "A")
    b = await _add_chapter(repo, project.id, act1, "B")
    c = await _add_chapter(repo, project.id, act1, "C")

    assert await _timeline_pairs(db_session, project.id) == {(a, b), (b, c)}


async def test_chain_follows_a_reorder(db_session: AsyncSession, test_user: dict):
    project, repo, (act1,) = await _project(db_session, test_user)
    a = await _add_chapter(repo, project.id, act1, "A")
    b = await _add_chapter(repo, project.id, act1, "B")
    c = await _add_chapter(repo, project.id, act1, "C")

    # A, C, B
    await repo.sync_editor_data(project.id, {"chapters": {"updated": [
        {"id": c, "sort_order": 2},
        {"id": b, "sort_order": 3},
    ]}})

    assert await _timeline_pairs(db_session, project.id) == {(a, c), (c, b)}


async def test_chain_spans_acts_in_narrative_order(db_session: AsyncSession, test_user: dict):
    project, repo, (act1, act2) = await _project(db_session, test_user, acts=2)
    a = await _add_chapter(repo, project.id, act1, "A1")
    b = await _add_chapter(repo, project.id, act2, "B1")

    assert await _timeline_pairs(db_session, project.id) == {(a, b)}


async def test_projection_is_idempotent(db_session: AsyncSession, test_user: dict):
    project, repo, (act1,) = await _project(db_session, test_user)
    await _add_chapter(repo, project.id, act1, "A")
    await _add_chapter(repo, project.id, act1, "B")
    before = await _timeline_pairs(db_session, project.id)

    result = await reconcile_timeline_chain(db_session, project.id)
    await db_session.commit()

    assert result["created"] == 0 and result["deleted"] == 0
    assert await _timeline_pairs(db_session, project.id) == before


async def test_non_timeline_edges_are_untouched(db_session: AsyncSession, test_user: dict):
    project, repo, (act1,) = await _project(db_session, test_user)
    a = await _add_chapter(repo, project.id, act1, "A")
    b = await _add_chapter(repo, project.id, act1, "B")
    edge_id = str(uuid.uuid4())
    await repo.sync_editor_data(project.id, {"edges": {"created": [
        {"id": edge_id, "source_id": a, "target_id": b, "edge_type": "causal", "label": "因为"}
    ]}})

    # an order change re-projects the timeline, the causal edge must survive
    await _add_chapter(repo, project.id, act1, "C")

    rows = (await db_session.execute(
        select(ChapterEdge).where(ChapterEdge.id == uuid.UUID(edge_id))
    )).scalars().all()
    assert len(rows) == 1 and rows[0].edge_type == "causal"


async def test_a_stray_timeline_edge_is_replaced_on_the_next_order_change(
    db_session: AsyncSession, test_user: dict
):
    project, repo, (act1,) = await _project(db_session, test_user)
    a = await _add_chapter(repo, project.id, act1, "A")
    b = await _add_chapter(repo, project.id, act1, "B")
    # 手画的、与顺序相反的时序边：只在边的同步里不会被清掉
    await repo.sync_editor_data(project.id, {"edges": {"created": [
        {"id": str(uuid.uuid4()), "source_id": b, "target_id": a, "edge_type": "timeline"}
    ]}})
    assert (b, a) in await _timeline_pairs(db_session, project.id)

    await _add_chapter(repo, project.id, act1, "C")

    pairs = await _timeline_pairs(db_session, project.id)
    assert (b, a) not in pairs
    assert (a, b) in pairs


async def test_relink_timeline_tool(db_session: AsyncSession, test_user: dict):
    from app.agent.tools.project_admin_tools import RelinkTimelineTool

    project, repo, (act1,) = await _project(db_session, test_user)
    await _add_chapter(repo, project.id, act1, "A")
    await _add_chapter(repo, project.id, act1, "B")

    result = await RelinkTimelineTool().run(
        db=db_session, project_id=str(project.id), user_id=str(test_user["id"]),
    )
    assert result.success, result.error
    assert result.data["chain_length"] == 1
    assert result.data["created"] == 0 and result.data["deleted"] == 0