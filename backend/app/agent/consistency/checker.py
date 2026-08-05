"""Backward-compatible facade over the v2 consistency pipeline.

v1 crammed the whole novel into three prompts and treated an empty LLM
response as a failure (see 一致性分析引擎v2设计文档 §1.2). The real work now
lives in :mod:`app.agent.consistency.engine` — a Map-Reduce-Verify pipeline
that extracts per-scene facts, discovers conflicts in code, and judges them
in isolated calls. This class keeps ``ConsistencyChecker.check_all`` as the
stable entry point used by REST / MCP / agent-tools callers.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency.engine import ConsistencyPipeline
from app.agent.consistency.models import ConsistencyReport
from app.llm.client import LLMClient

ProgressCb = Callable[[str, int, int, str], Awaitable[None]]


class ConsistencyChecker:
    def __init__(self, db: AsyncSession, llm_client: LLMClient | None = None):
        self.db = db
        self._llm = llm_client

    async def check_all(
        self,
        project_id: str,
        requested_by: str | None = None,
        progress_cb: ProgressCb | None = None,
    ) -> ConsistencyReport:
        pipeline = ConsistencyPipeline(self.db, self._llm, progress_cb)
        return await pipeline.run(project_id, requested_by=requested_by)
