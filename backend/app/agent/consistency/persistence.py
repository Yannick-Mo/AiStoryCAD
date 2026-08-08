"""Persistence helpers for the consistency engine v3.

v2's scene-fact cache is gone — the ledger tables are written by
:mod:`app.agent.consistency.worker` and reconciled by
:mod:`app.agent.consistency.reconcile`. What remains here is the *check-side*
persistence: report rows, issue linkage, resolve flow.

Design rule (§14.5): ``check_all`` gathers everything in ONE transaction and
commits in 阶段5 — no half-written reports. Report persistence therefore does
not commit; the caller (checker) commits.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency.models import ConsistencyIssue, ConsistencyReport, Verdict
from app.agent.consistency.orm import (
    ConflictCandidateRecord,
    ConsistencyLog,
    ConsistencyReportRecord,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reports / issues
# ---------------------------------------------------------------------------

async def latest_report(db: AsyncSession, project_id) -> ConsistencyReportRecord | None:
    result = await db.execute(
        select(ConsistencyReportRecord)
        .where(ConsistencyReportRecord.project_id == project_id)
        .order_by(ConsistencyReportRecord.created_at.desc(), ConsistencyReportRecord.id.desc())
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
        .order_by(ConsistencyReportRecord.created_at.desc(), ConsistencyReportRecord.id.desc())
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
    """One report row + its issue rows, then link judged candidates via
    ``issue_id`` so ``resolve`` can flip them (阶段5, §9.1). Does NOT commit —
    caller commits at the end of the check.
    """
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
        log = _issue_to_log(project_id, record.id, issue)
        db.add(log)
        await db.flush()
        candidate_id = getattr(issue, "candidate_id", None)
        if candidate_id:
            try:
                await db.execute(
                    update(ConflictCandidateRecord)
                    .where(ConflictCandidateRecord.id == candidate_id)
                    .values(issue_id=log.id)
                )
            except Exception:
                logger.warning("failed to link issue to candidate %s", candidate_id, exc_info=True)
    return record


def _issue_to_log(project_id, report_id, issue: ConsistencyIssue) -> ConsistencyLog:
    entity_id = None
    if issue.entity_id:
        try:
            entity_id = uuid.UUID(issue.entity_id.split(",")[0].strip())
        except (ValueError, AttributeError):
            entity_id = None
    candidate_id = getattr(issue, "candidate_id", None)
    evidence = issue.evidence
    if candidate_id is not None and evidence is None:
        evidence = []
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
        evidence=evidence,
    )


async def resolve_issue(db: AsyncSession, issue_id: str, resolved: bool = True) -> bool:
    row = await db.get(ConsistencyLog, issue_id)
    if row is None:
        return False
    row.is_resolved = resolved
    # 联动: flip the candidate that produced this issue so it never
    # resurrects as pending (§9.1).
    await db.execute(
        update(ConflictCandidateRecord)
        .where(ConflictCandidateRecord.issue_id == issue_id)
        .values(
            resolved=resolved,
            status=("dismissed" if resolved else "verified"),
            resolved_at=datetime.now(timezone.utc) if resolved else None,
        )
    )
    return True


async def get_issue(db: AsyncSession, issue_id: str) -> ConsistencyLog | None:
    return await db.get(ConsistencyLog, issue_id)


async def rebuild_report(db: AsyncSession, project_id) -> ConsistencyReport | None:
    """Reconstruct the latest full report from its persisted issue rows."""
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