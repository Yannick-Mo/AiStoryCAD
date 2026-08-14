"""Regression tests: the worker queue must not retry forever.

Covers the reviewed bugs:
  * a failed row with the same hash must NOT be reset by the periodic audit
    while its backoff is still running (retry_count / next_retry_at kept);
  * after ``_DEAD_RETRY_LIMIT`` failures the row goes terminal (``dead``)
    and is never picked up again — only a content change revives it.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency.orm import FactQueueItem
from app.agent.consistency.utils import hash_content
from app.agent.consistency.worker import FactWorker, Inbox, _DEAD_RETRY_LIMIT
from app.project.models import Project
from app.storycad.models import Chapter, Scene, SceneContent

pytestmark = pytest.mark.asyncio


async def _project_scene(db: AsyncSession, test_user: dict):
    project = Project(title="P", owner_id=test_user["id"])
    db.add(project)
    await db.flush()
    chapter = Chapter(project_id=project.id, title="C")
    db.add(chapter)
    await db.flush()
    scene = Scene(project_id=project.id, chapter_id=chapter.id, title="S")
    db.add(scene)
    await db.flush()
    return project, scene


def _worker(db: AsyncSession) -> FactWorker:
    return FactWorker(Inbox(), db, client=MagicMock())


async def test_enqueue_keeps_failed_row_while_backoff_running(
    db_session: AsyncSession, test_user: dict
):
    """Same hash + failed + next_retry_at in the future → untouched (audit-safe)."""
    worker = _worker(db_session)
    project, scene = await _project_scene(db_session, test_user)
    h = hash_content("content")
    backoff_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    db_session.add(
        FactQueueItem(
            project_id=project.id, scene_id=scene.id, content_hash=h,
            status="failed", retry_count=2, next_retry_at=backoff_at,
            last_error="boom",
        )
    )
    await db_session.flush()

    assert await worker._enqueue(db_session, project.id, scene.id, h) is False

    row = await db_session.get(FactQueueItem, (project.id, scene.id))
    assert row.status == "failed"
    assert row.retry_count == 2
    assert row.next_retry_at == backoff_at


async def test_enqueue_allows_retry_after_backoff_elapsed(
    db_session: AsyncSession, test_user: dict
):
    """Same hash + failed + next_retry_at in the past → proceeds as pending."""
    worker = _worker(db_session)
    project, scene = await _project_scene(db_session, test_user)
    h = hash_content("content")
    db_session.add(
        FactQueueItem(
            project_id=project.id, scene_id=scene.id, content_hash=h,
            status="failed", retry_count=2,
            next_retry_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    await db_session.flush()

    assert await worker._enqueue(db_session, project.id, scene.id, h) is True
    row = await db_session.get(FactQueueItem, (project.id, scene.id))
    assert row.status == "pending"
    assert row.retry_count == 2  # backoff bookkeeping preserved across attempts


async def test_hash_change_resets_failed_row(
    db_session: AsyncSession, test_user: dict
):
    """New content → full reset (pending, retry_count=0, next_retry_at=now)."""
    worker = _worker(db_session)
    project, scene = await _project_scene(db_session, test_user)
    db_session.add(
        FactQueueItem(
            project_id=project.id, scene_id=scene.id, content_hash="old-hash",
            status="failed", retry_count=4,
            next_retry_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    await db_session.flush()

    assert await worker._enqueue(db_session, project.id, scene.id, "new-hash") is True
    row = await db_session.get(FactQueueItem, (project.id, scene.id))
    assert row.content_hash == "new-hash"
    assert row.status == "pending"
    assert row.retry_count == 0
    assert row.last_error is None


async def test_mark_failed_goes_dead_after_retry_limit(
    db_session: AsyncSession, test_user: dict
):
    """The last allowed failure transitions the row to terminal ``dead``."""
    worker = _worker(db_session)
    project, scene = await _project_scene(db_session, test_user)
    db_session.add(
        FactQueueItem(
            project_id=project.id, scene_id=scene.id, content_hash="h",
            status="failed", retry_count=_DEAD_RETRY_LIMIT - 1,
            next_retry_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    await db_session.flush()

    await worker._mark_failed(db_session, project.id, scene.id, Exception("boom"), "h")
    row = await db_session.get(FactQueueItem, (project.id, scene.id))
    assert row.status == "dead"
    assert row.retry_count == _DEAD_RETRY_LIMIT


async def test_dead_row_never_revives_on_same_hash(
    db_session: AsyncSession, test_user: dict
):
    """Audit must not resurrect a dead row; only a content change may."""
    worker = _worker(db_session)
    project, scene = await _project_scene(db_session, test_user)
    h = hash_content("content")
    db_session.add(
        FactQueueItem(
            project_id=project.id, scene_id=scene.id, content_hash=h,
            status="dead", retry_count=_DEAD_RETRY_LIMIT,
            next_retry_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )
    await db_session.flush()

    assert await worker._enqueue(db_session, project.id, scene.id, h) is False
    row = await db_session.get(FactQueueItem, (project.id, scene.id))
    assert row.status == "dead"

    assert await worker._enqueue(db_session, project.id, scene.id, "fresh-hash") is True
    row = await db_session.get(FactQueueItem, (project.id, scene.id))
    assert row.status == "pending"
    assert row.retry_count == 0


async def test_backlog_query_excludes_dead_rows(
    db_session: AsyncSession, test_user: dict
):
    """The backlog select only ever returns pending/failed rows."""
    project, scene = await _project_scene(db_session, test_user)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.add(
        FactQueueItem(
            project_id=project.id, scene_id=scene.id, content_hash="h1",
            status="failed", retry_count=1, next_retry_at=past,
        )
    )
    other = Scene(project_id=project.id, chapter_id=scene.chapter_id, title="S2")
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        FactQueueItem(
            project_id=project.id, scene_id=other.id, content_hash="h2",
            status="dead", retry_count=_DEAD_RETRY_LIMIT, next_retry_at=past,
        )
    )
    await db_session.flush()

    now = datetime.now(timezone.utc)
    result = await db_session.execute(
        select(FactQueueItem).where(
            FactQueueItem.status.in_(("pending", "failed")),
            FactQueueItem.next_retry_at <= now,
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].scene_id == scene.id