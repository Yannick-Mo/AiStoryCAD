"""Candidate reconciliation and check-time hash reconciliation (§6 阶段1, §5.4).

This module is deliberately LLM-free: cluster discovery, pair upsert,
archival and hash drift detection are pure SQL/code.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency.facts import dedupe_pairs
from app.agent.consistency.orm import (
    ConflictCandidateRecord,
    ConsistencyFact,
    FactQueueItem,
)
from app.agent.consistency.utils import hash_content
from app.config import settings as default_settings
from app.storycad.models import SceneContent

logger = logging.getLogger(__name__)

_UNIQUE_CANDIDATE_COLS = ["project_id", "entity", "attribute", "value_a", "value_b"]


def _pid(project_id) -> uuid.UUID:
    return project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(project_id)


async def reconcile_project_hash(
    db: AsyncSession,
    project_id,
    worker=None,
    settings=default_settings,
) -> dict:
    """Check-before hash reconciliation (§5.4, §6 阶段1 前置).

    Compares ``scene_contents`` against ``consistency_fact_queue`` locally
    (sha256 only, no LLM) and upserts drifted scenes into the queue. Deletes
    queue rows whose scene vanished, and re-arms orphaned `processing` rows.

    Returns ``{"drift": int}``. Runs inside *db* and commits its own changes
    (the queue rows must be visible to the worker before the check waits).
    """
    pid = _pid(project_id)
    now = datetime.now(timezone.utc)

    contents = await db.execute(
        select(SceneContent.scene_id, SceneContent.content).where(
            SceneContent.project_id == pid
        )
    )
    expected: dict[uuid.UUID, str] = {}
    for row in contents.all():
        expected[row.scene_id] = hash_content(row.content or "")

    queue_rows = await db.execute(
        select(FactQueueItem).where(FactQueueItem.project_id == pid)
    )
    current = {r.scene_id: r for r in queue_rows.scalars().all()}

    drift = 0
    for scene_id, h in expected.items():
        row = current.get(scene_id)
        if row is None:
            db.add(
                FactQueueItem(
                    project_id=pid,
                    scene_id=scene_id,
                    content_hash=h,
                    status="pending",
                    next_retry_at=now,
                )
            )
            drift += 1
        elif row.content_hash != h:
            row.content_hash = h
            row.status = "pending"
            row.next_retry_at = now
            row.retry_count = 0
            row.last_error = None
            row.updated_at = now
            drift += 1
        # same hash → nothing to do (zero-cost gate)

    stale = set(current) - set(expected)
    for scene_id in stale:
        await db.execute(
            sa_delete(FactQueueItem).where(
                FactQueueItem.project_id == pid,
                FactQueueItem.scene_id == scene_id,
            )
        )

    # Recovery: `processing` rows orphaned by a crash.
    await db.execute(
        FactQueueItem.__table__.update()
        .where(
            FactQueueItem.project_id == pid,
            FactQueueItem.status == "processing",
            FactQueueItem.updated_at < now - timedelta(minutes=5),
        )
        .values(status="pending", next_retry_at=now)
    )
    await db.commit()
    return {"drift": drift}


async def project_queue_depth(db: AsyncSession, project_id) -> int:
    pid = _pid(project_id)
    result = await db.execute(
        select(func.count())
        .select_from(FactQueueItem)
        .where(
            FactQueueItem.project_id == pid,
            FactQueueItem.status.in_(("pending", "processing")),
        )
    )
    return int(result.scalar_one() or 0)


async def reconcile_project(
    db: AsyncSession,
    project_id,
    settings=default_settings,
) -> dict:
    """Authoritative phase-1 candidate reconciliation (§6 阶段1, §14.3).

    1. Read all active facts, group per ``(entity, attribute)`` cluster.
    2. For every multi-value cluster, enumerate each distinct value pair and
       upsert the candidate row (dictionary-order normalised), refreshing
       ``last_seen_at`` and rewriting the evidence snapshot.
    3. ``pending`` candidates whose pair is no longer among the pairs → archived.
    4. Cluster distinct values > cap → keep the most recent *cap*, note it.

    Pure SQL/code. Returns ``{"clusters", "pairs_seen", "pairs_archived", "truncated"}``.
    """
    pid = _pid(project_id)
    cap = settings.consistency_cluster_cap
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(
            ConsistencyFact.entity,
            ConsistencyFact.attribute,
            ConsistencyFact.value_norm,
            ConsistencyFact.evidence,
            ConsistencyFact.scene_id,
            ConsistencyFact.chapter_id,
            ConsistencyFact.created_at,
        )
        .where(ConsistencyFact.project_id == pid, ConsistencyFact.is_active.is_(True))
        .order_by(ConsistencyFact.created_at.desc(), ConsistencyFact.id)
    )
    rows = result.all()

    # (entity, attribute) → value_norm → most-recent evidence row.
    clusters: dict[tuple[str, str], dict[str, tuple]] = defaultdict(dict)
    for entity, attribute, value_norm, evidence, scene_id, chapter_id, created_at in rows:
        if not value_norm:
            continue
        key = (entity, attribute)
        if value_norm not in clusters[key]:
            clusters[key][value_norm] = (
                evidence or "", scene_id, chapter_id, created_at
            )

    truncated: list[str] = []
    seen_pairs: set[tuple[str, str, str, str]] = set()

    for (entity, attribute), values in clusters.items():
        if len(values) > cap:
            # 簇容量护栏: keep only the most recent `cap` distinct values.
            truncated.append(attribute)
            kept = sorted(values.items(), key=lambda kv: kv[1][3], reverse=True)[:cap]
            values = dict(kept)
        distinct = sorted(values.keys())
        if len(distinct) < 2:
            continue
        pairs = dedupe_pairs(
            [(entity, attribute, a, b) for a in distinct for b in distinct if a != b]
        )
        for e, a, va, vb in pairs:
            seen_pairs.add((e, a, va, vb))
            snap_a = values[va]
            snap_b = values[vb]
            stmt = pg_insert(ConflictCandidateRecord).values(
                project_id=pid,
                entity=e,
                attribute=a,
                value_a=va,
                value_b=vb,
                status="pending",
                evidence_a=snap_a[0][:400],
                scene_a=snap_a[1],
                chapter_a=snap_a[2],
                evidence_b=snap_b[0][:400],
                scene_b=snap_b[1],
                chapter_b=snap_b[2],
                last_seen_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=_UNIQUE_CANDIDATE_COLS,
                set_={
                    "last_seen_at": now,
                    # 复活的候选:archived → pending;verified/dismissed 保留人工裁定。
                    "status": case(
                        (ConflictCandidateRecord.status == "archived", "pending"),
                        else_=ConflictCandidateRecord.status,
                    ),
                    "evidence_a": snap_a[0][:400],
                    "scene_a": snap_a[1],
                    "chapter_a": snap_a[2],
                    "evidence_b": snap_b[0][:400],
                    "scene_b": snap_b[1],
                    "chapter_b": snap_b[2],
                },
            )
            await db.execute(stmt)

    # Archive: pending candidates whose pair no longer coexists.
    pending_result = await db.execute(
        select(ConflictCandidateRecord).where(
            ConflictCandidateRecord.project_id == pid,
            ConflictCandidateRecord.status == "pending",
        )
    )
    archived = 0
    for cand in pending_result.scalars().all():
        key = (cand.entity, cand.attribute, cand.value_a, cand.value_b)
        if key in seen_pairs:
            continue
        cand.status = "archived"
        cand.last_seen_at = now
        archived += 1

    await db.commit()
    return {
        "clusters": len(clusters),
        "pairs_seen": len(seen_pairs),
        "pairs_archived": archived,
        "truncated": truncated,
    }