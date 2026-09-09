"""Keep the main timeline in sync with the chapter order.

``sort_order`` is the single source of truth for order; the timeline edges are
a *projection* of it — one straight chain that follows the reading order (act
order, then in-act order).  The plot board then shows the chapters connected in
the order they are read, which is what makes it legible.

Only ``edge_type == "timeline"`` edges are touched.  Causal / foreshadow /
character edges are user content and stay untouched, and so do timeline edges
that already match the chain — so their ids, labels and handles survive a
re-projection, and calling this twice changes nothing.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storycad.models import Act, Chapter, ChapterEdge
from app.storycad.order import order_by_sequence

logger = logging.getLogger(__name__)

TIMELINE = "timeline"


async def ordered_chapter_ids(db: AsyncSession, project_id: uuid.UUID) -> list[uuid.UUID]:
    """Chapter ids in reading order: act order, then in-act order."""
    result = await db.execute(
        select(Chapter.id)
        .outerjoin(Act, Act.id == Chapter.act_id)
        .where(Chapter.project_id == project_id)
        .order_by(Act.sort_order.asc(), *order_by_sequence(Chapter))
    )
    return [row[0] for row in result.all()]


async def reconcile_timeline_chain(db: AsyncSession, project_id: uuid.UUID) -> dict:
    """Rebuild the main timeline as one chain following the chapter order.

    Returns ``{"created": n, "deleted": n, "kept": n, "chain": [[src, tgt], ...]}``.
    The caller owns the transaction — this only flushes, it never commits.
    """
    ordered = await ordered_chapter_ids(db, project_id)
    desired: list[tuple[uuid.UUID, uuid.UUID]] = list(zip(ordered, ordered[1:]))
    desired_set = set(desired)

    existing = (await db.execute(
        select(ChapterEdge).where(
            ChapterEdge.project_id == project_id,
            ChapterEdge.edge_type == TIMELINE,
        )
    )).scalars().all()

    kept: set[tuple[uuid.UUID, uuid.UUID]] = set()
    deleted = 0
    for edge in existing:
        pair = (edge.source_id, edge.target_id)
        if pair in desired_set:
            kept.add(pair)
            continue
        await db.delete(edge)
        deleted += 1

    created = 0
    for pair in desired:
        if pair in kept:
            continue
        db.add(ChapterEdge(
            project_id=project_id,
            source_id=pair[0],
            target_id=pair[1],
            edge_type=TIMELINE,
            label="",
            # 空 handle = 由画布按节点位置自动选最佳端点
            source_handle="",
            target_handle="",
        ))
        created += 1

    if created or deleted:
        await db.flush()
        logger.info(
            "timeline relinked for project %s: +%d / -%d edges", project_id, created, deleted
        )
    return {
        "created": created,
        "deleted": deleted,
        "kept": len(kept),
        "chain": [[str(src), str(tgt)] for src, tgt in desired],
    }