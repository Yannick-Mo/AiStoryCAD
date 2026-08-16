"""In-memory job registry for long-running consistency checks.

A full cold-run of the v2 pipeline can take minutes, so REST returns a
202 + ``job_id`` and the frontend follows progress over SSE/polling. Jobs
are process-local (single uvicorn worker) — the same assumption the API's
token blacklist already makes. A completed job keeps its report in memory;
finished reports are also persisted to ``consistency_reports`` for history.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

SSE_PING_INTERVAL = 15
MAX_KEPT_JOBS = 100
# Each running job holds a Task, DB session, events queue and report dict —
# cap concurrent running jobs per user so memory stays bounded.
MAX_RUNNING_JOBS_PER_USER = 3


@dataclass
class ConsistencyJob:
    job_id: str
    project_id: str
    user_id: str
    state: str = "running"          # running | done | failed
    stage: str = "pending"
    progress: dict = field(default_factory=lambda: {"done": 0, "total": 1})
    message: str = ""
    report: dict | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    task: asyncio.Task | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "project_id": self.project_id,
            "state": self.state,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "report": self.report,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, ConsistencyJob] = {}
        self._lock = asyncio.Lock()

    def create(self, project_id: str, user_id: str) -> ConsistencyJob:
        job = ConsistencyJob(job_id=str(uuid.uuid4()), project_id=project_id, user_id=user_id)
        self._jobs[job.job_id] = job
        self._prune()
        return job

    def get(self, job_id: str) -> ConsistencyJob | None:
        return self._jobs.get(job_id)

    async def get_or_create(self, project_id: str, user_id: str) -> tuple[ConsistencyJob | None, str]:
        """Atomic get-or-create under the registry lock (no TOCTOU duplicates).

        Returns ``(job, status)`` where status is one of:
          * "created" — a new job was registered for this user
          * "reused"  — an in-flight job owned by this user was returned
          * "busy"    — another user's check is in flight, or this user is at
            MAX_RUNNING_JOBS_PER_USER; job is None (do not leak job_id).
        """
        async with self._lock:
            for job in self._jobs.values():
                if job.project_id == project_id and job.state == "running":
                    if job.user_id == user_id:
                        return job, "reused"
                    return None, "busy"
            running = sum(
                1 for j in self._jobs.values()
                if j.state == "running" and j.user_id == user_id
            )
            if running >= MAX_RUNNING_JOBS_PER_USER:
                return None, "busy"
            return self.create(project_id, user_id), "created"

    def mark_done(self, job: ConsistencyJob, report: dict) -> None:
        job.state = "done"
        job.stage = "done"
        job.report = report
        job.finished_at = datetime.now(timezone.utc)
        self._push(job, "done", {"job_id": job.job_id, "report": report})
        self._prune()

    def mark_failed(self, job: ConsistencyJob, error: str) -> None:
        job.state = "failed"
        job.error = error
        job.finished_at = datetime.now(timezone.utc)
        self._push(job, "error", {"job_id": job.job_id, "message": error})
        self._prune()

    def _prune(self) -> None:
        """Keep the most recent finished jobs, drop older ones (memory bound).

        The report dict is already persisted to ``consistency_reports``, so a
        pruned job loses nothing but its in-memory copy.
        """
        finished = sorted(
            (j for j in self._jobs.values() if j.state in ("done", "failed")),
            key=lambda j: j.finished_at or j.created_at,
            reverse=True,
        )
        for job in finished[MAX_KEPT_JOBS:]:
            self._jobs.pop(job.job_id, None)

    def update_progress(self, job: ConsistencyJob, stage: str, done: int, total: int, message: str) -> None:
        job.stage = stage
        job.progress = {"done": done, "total": total}
        job.message = message
        self._push(
            job,
            "progress",
            {"job_id": job.job_id, "stage": stage, "progress": job.progress, "message": message},
        )

    @staticmethod
    def _push(job: ConsistencyJob, event: str, data: dict) -> None:
        try:
            job.events.put_nowait((event, data))
        except asyncio.QueueFull:
            # Drop oldest so the frontend always sees the latest state.
            try:
                job.events.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                job.events.put_nowait((event, data))
            except asyncio.QueueFull:
                pass


_job_manager = JobManager()


def get_job_manager() -> JobManager:
    return _job_manager
