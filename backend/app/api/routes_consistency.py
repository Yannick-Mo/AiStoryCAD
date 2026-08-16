"""Consistency analysis REST API.

Endpoints
---------
POST /projects/{project_id}/check?sync=false
    Submit a check. 200 with a report when the project is unchanged since the
    last run (or ``sync=true``), otherwise 202 + ``{job_id}``. Project
    ownership is verified first (was missing in v1 — an IDOR).
GET  /jobs/{job_id}            job status (+ report when done)
GET  /jobs/{job_id}/events     SSE progress stream
GET  /projects/{project_id}/reports   check history
POST /issues/{issue_id}/resolve       mark an issue resolved

All routes require the authenticated user to own the referenced project.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consistency_jobs import SSE_PING_INTERVAL, ConsistencyJob, get_job_manager
from app.api.deps import get_current_user, get_db
from app.api.rate_limiter import rate_limiter
from app.agent.consistency.checker import ConsistencyChecker
from app.agent.consistency.models import ConsistencyReport
from app.agent.consistency.persistence import (
    content_stale_since,
    get_issue,
    list_reports,
    rebuild_report,
    resolve_issue,
)
from app.agent.tools.base import verify_project_owner as _verify_tool_owner

router = APIRouter(prefix="/api/consistency", tags=["consistency"])


async def _verify_project_owner(db: AsyncSession, project_id: uuid.UUID, user: dict) -> None:
    try:
        await _verify_tool_owner(db, project_id, user["id"])
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized to access this project")


def _format_sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def _require_job_owner(job: ConsistencyJob, user: dict) -> None:
    if job.user_id and str(job.user_id) != str(user["id"]):
        raise HTTPException(status_code=403, detail="Not authorized to access this job")


def _job_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Job not found")


@router.post("/projects/{project_id}/check")
async def check_consistency(
    project_id: uuid.UUID,
    request: Request,
    sync: bool = False,
    force: bool = False,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_project_owner(db, project_id, user)
    if not await rate_limiter.check(f"consistency_check:{user['id']}", max_attempts=30, window=60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    pid_str = str(project_id)

    # Synchronous path (tests / debugging / MCP-style callers).
    if sync:
        from app.database import async_session

        async with async_session() as session:
            checker = ConsistencyChecker(session)
            report = await checker.check_all(pid_str, requested_by=user["id"])
        return report.model_dump(mode="json")

    manager = get_job_manager()

    # Freshness shortcut: if nothing changed since the last report, serve it.
    # `force` bypasses this so the user can actually request a fresh run
    # (previously "重新检查" was a no-op that returned the cached report).
    latest = None
    if not force:
        latest = await rebuild_report(db, pid_str)
        if latest is not None and latest.timestamp is not None:
            if not await content_stale_since(db, project_id, latest.timestamp):
                return latest.model_dump(mode="json")

    # Reuse an in-flight job for the same project instead of double-running.
    # Atomic get-or-create under the registry lock: two concurrent requests
    # can no longer both create a job (TOCTOU), and another user's job_id is
    # never leaked — that case 429s instead.
    job, status = await manager.get_or_create(pid_str, str(user["id"]))
    if status == "busy":
        raise HTTPException(status_code=429, detail="该项目已有进行中的检查，请稍后再试")
    if status == "reused":
        return {"job_id": job.job_id, "state": job.state, "reusing": True}

    async def _run_job() -> None:
        from app.database import async_session

        try:
            async with async_session() as session:
                async def _cb(stage: str, done: int, total: int, message: str) -> None:
                    manager.update_progress(job, stage, done, total, message)

                checker = ConsistencyChecker(session)
                report = await checker.check_all(pid_str, requested_by=user["id"], progress_cb=_cb)
                manager.mark_done(job, report.model_dump(mode="json"))
        except Exception as exc:
            manager.mark_failed(job, str(exc))

    job.task = asyncio.create_task(_run_job())
    return {"job_id": job.job_id, "state": job.state, "reusing": False}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(get_current_user)):
    job = get_job_manager().get(job_id)
    if job is None:
        raise _job_not_found()
    await _require_job_owner(job, user)
    return job.to_dict()


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, user: dict = Depends(get_current_user)):
    job = get_job_manager().get(job_id)
    if job is None:
        raise _job_not_found()
    await _require_job_owner(job, user)

    async def _stream() -> AsyncGenerator[str, None]:
        yield "retry: 3000\n\n"
        # Replay terminal state for late subscribers.
        if job.state == "done":
            yield _format_sse("done", {"job_id": job.job_id, "report": job.report})
            return
        if job.state == "failed":
            yield _format_sse("error", {"job_id": job.job_id, "message": job.error})
            return
        while True:
            if await request.is_disconnected():
                break
            try:
                event, data = await asyncio.wait_for(job.events.get(), timeout=SSE_PING_INTERVAL)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    break
                yield "event: ping\ndata: {}\n\n"
                continue
            yield _format_sse(event, data)
            if event in ("done", "error"):
                break

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/projects/{project_id}/reports")
async def get_reports(
    project_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_project_owner(db, project_id, user)
    records = await list_reports(db, project_id)
    return [
        {
            "id": str(r.id),
            "summary": r.summary,
            "stats": r.stats,
            "meta": r.meta,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


@router.post("/projects/{project_id}/reconcile")
async def reconcile(
    project_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger hash reconcile + candidate reconcile (§9.1, §14.6).

    运营/调试用。返回核对统计与本次漂移场景数。
    """
    await _verify_project_owner(db, project_id, user)
    from app.agent.consistency.reconcile import (
        reconcile_project,
        reconcile_project_hash,
    )

    hash_stats = await reconcile_project_hash(db, project_id)
    stats = await reconcile_project(db, project_id)
    facts_active = 0
    from app.agent.consistency.orm import ConsistencyFact
    from app.agent.consistency.reconcile import project_queue_depth

    await db.commit()
    fact_row = await db.execute(
        select(func.count()).select_from(ConsistencyFact).where(
            ConsistencyFact.project_id == project_id,
            ConsistencyFact.is_active.is_(True),
        )
    )
    facts_active = int(fact_row.scalar_one_or_none() or 0)

    return {
        "ok": True,
        "stats": {
            "facts_active": facts_active,
            "clusters": stats.get("clusters", 0),
            "pairs_created": stats.get("pairs_seen", 0),
            "pairs_archived": stats.get("pairs_archived", 0),
            "drift_enqueued": hash_stats.get("drift", 0),
        },
    }


@router.get("/projects/{project_id}/live")
async def live_hints(
    project_id: uuid.UUID,
    scene_id: uuid.UUID | None = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """编辑期间内联提示:返回该场景的写时探查命中(§9.2)。"""
    from datetime import datetime, timezone

    await _verify_project_owner(db, project_id, user)
    checker = ConsistencyChecker(db)
    hints = await checker.live_hint(str(project_id), str(scene_id), None)
    return {"scene_id": str(scene_id), "candidates": hints}


@router.post("/issues/{issue_id}/resolve")
async def resolve_consistency_issue(
    issue_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    issue = await get_issue(db, str(issue_id))
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.project_id is None:
        raise HTTPException(status_code=403, detail="Issue is not bound to a project")
    await _verify_project_owner(db, issue.project_id, user)
    ok = await resolve_issue(db, str(issue_id))
    await db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="Issue not found")
    return {"ok": True, "issue_id": str(issue_id), "is_resolved": True}
