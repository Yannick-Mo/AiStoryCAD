"""Persistence helpers for the consistency engine v2.

Keeps the SQL out of the engine so the pipeline stays readable and the
caching rules are centralised and testable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency.models import ConsistencyIssue, ConsistencyReport
from app.agent.consistency.orm import ConsistencyLog, ConsistencyReportRecord, SceneFactCache


# ---------------------------------------------------------------------------
# Scene fact cache
# ---------------------------------------------------------------------------

async def load_scene_fact_cache(
    db: AsyncSession,
    project_id,
    scene_ids: list[str],
) -> dict[str, dict]:
    """Load cached facts for the given scene ids, keyed by ``str(scene_id)``.

    Returns ``{scene_id_str: {"content_hash": ..., "facts": [...], "error": ...}}``.
    """
    if not scene_ids:
        return {}
    result = await db.execute(
        select(SceneFactCache).where(
            SceneFactCache.project_id == project_id,
            SceneFactCache.scene_id.in_(scene_ids),
        )
    )
    out: dict[str, dict] = {}
    for row in result.scalars().all():
        out[str(row.scene_id)] = {
            "content_hash": row.content_hash,
            "facts": row.facts or [],
            "error": row.error,
        }
    return out


async def save_scene_fact_cache(
    db: AsyncSession,
    project_id,
    entries: list[dict],
) -> None:
    """Upsert scene fact cache rows.

    Each entry: ``{"scene_id", "content_hash", "facts", "error"}``. Only
    scenes with a non-empty content hash are written (empty scenes carry no
    extractable body and don't belong in the cache).
    """
    for entry in entries:
        if not entry.get("content_hash"):
            continue
        cached = await db.get(SceneFactCache, entry["scene_id"])
        if cached is None:
            cached = SceneFactCache(scene_id=entry["scene_id"], project_id=project_id)
            db.add(cached)
        cached.content_hash = entry["content_hash"]
        cached.facts = entry.get("facts") or []
        cached.error = entry.get("error")
        cached.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Reports / issues
# ---------------------------------------------------------------------------

async def latest_report(db: AsyncSession, project_id) -> ConsistencyReportRecord | None:
    result = await db.execute(
        select(ConsistencyReportRecord)
        .where(ConsistencyReportRecord.project_id == project_id)
        .order_by(ConsistencyReportRecord.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_reports(
    db: AsyncSession,
    project_id,
    limit: int = 20,
) -> list[ConsistencyReportRecord]:
    result = await db.execute(
        select(ConsistencyReportRecord)
        .where(ConsistencyReportRecord.project_id == project_id)
        .order_by(ConsistencyReportRecord.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def content_stale_since(db: AsyncSession, project_id, since: datetime) -> bool:
    """True when any narrative entity was modified after *since*.

    Used as a cheap freshness gate before re-running a check: if nothing
    changed since the last report, the cached report is served directly.
    """
    from app.project.models import Project
    from app.storycad.models import (
        Act,
        Chapter,
        ChapterEdge,
        ChapterRhythm,
        Character,
        CharacterRelation,
        Scene,
        SceneContent,
        Theme,
    )

    models = (
        Act, Chapter, Scene, SceneContent, Character,
        CharacterRelation, ChapterEdge, ChapterRhythm, Theme,
    )
    for model in models:
        if not hasattr(model, "updated_at"):
            continue
        row = await db.execute(
            select(func.max(model.updated_at)).where(model.project_id == project_id)
        )
        max_ts = row.scalar_one_or_none()
        if max_ts and max_ts.replace(tzinfo=timezone.utc) > since:
            return True
    proj_row = await db.execute(select(func.max(Project.updated_at)).where(Project.id == project_id))
    proj_max = proj_row.scalar_one_or_none()
    if proj_max and proj_max.replace(tzinfo=timezone.utc) > since:
        return True
    return False


async def persist_report(
    db: AsyncSession,
    project_id,
    requested_by: str | None,
    report: ConsistencyReport,
    meta: dict,
) -> ConsistencyReportRecord:
    record = ConsistencyReportRecord(
        project_id=project_id,
        requested_by=requested_by,
        summary=report.summary,
        stats=report.stats,
        meta=meta,
    )
    db.add(record)
    await db.flush()
    for issue in report.issues:
        db.add(_issue_to_log(project_id, record.id, issue))
    return record


def _issue_to_log(project_id, report_id, issue: ConsistencyIssue) -> ConsistencyLog:
    entity_id = None
    if issue.entity_id:
        try:
            import uuid
            entity_id = uuid.UUID(issue.entity_id.split(",")[0].strip())
        except (ValueError, AttributeError):
            entity_id = None
    return ConsistencyLog(
        project_id=project_id,
        report_id=report_id,
        check_type=issue.check_type,
        severity=issue.severity,
        entity_type=issue.entity_type,
        entity_id=entity_id,
        description=issue.description,
        suggestion=issue.suggestion,
        verdict=issue.verdict.value if issue.verdict else None,
        evidence=issue.evidence,
    )


async def resolve_issue(db: AsyncSession, issue_id: str, resolved: bool = True) -> bool:
    row = await db.get(ConsistencyLog, issue_id)
    if row is None:
        return False
    row.is_resolved = resolved
    return True


async def get_issue(db: AsyncSession, issue_id: str) -> ConsistencyLog | None:
    return await db.get(ConsistencyLog, issue_id)


async def rebuild_report(db: AsyncSession, project_id) -> ConsistencyReport | None:
    """Reconstruct the latest full report from its persisted issue rows.

    Used to serve a fresh report directly (HTTP 200) without re-running the
    pipeline when nothing has changed since the last check.
    """
    from app.agent.consistency.models import Verdict

    record = await latest_report(db, project_id)
    if record is None:
        return None
    rows = await db.execute(
        select(ConsistencyLog).where(ConsistencyLog.report_id == record.id)
    )
    issues: list[ConsistencyIssue] = []
    for r in rows.scalars().all():
        verdict = None
        if r.verdict:
            try:
                verdict = Verdict(r.verdict)
            except ValueError:
                verdict = None
        issues.append(
            ConsistencyIssue(
                check_type=r.check_type,
                severity=r.severity,
                entity_type=r.entity_type or "",
                entity_id=str(r.entity_id) if r.entity_id else None,
                description=r.description,
                suggestion=r.suggestion,
                verdict=verdict,
                evidence=r.evidence,
            )
        )
    return ConsistencyReport(
        project_id=project_id,
        issues=issues,
        summary=record.summary,
        stats=record.stats,
        meta=record.meta,
        timestamp=record.created_at,
    )
