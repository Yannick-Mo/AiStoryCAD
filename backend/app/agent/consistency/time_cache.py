"""Scene-time normalisation cache (§6 阶段3, §14.3).

``scene_time`` values are free text ("第三天傍晚", "早晨" …). Their semantic
order is resolved ONCE per project by a small LLM call, cached permanently
in ``consistency_time_cache``, and only *new* values are appended on later
checks. After the cache is warm, timeline ordering is pure code.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency import prompts
from app.agent.consistency.orm import ConsistencyTimeCache
from app.agent.consistency.utils import llm_json
from app.llm.client import LLMClient
from app.storycad.models import Scene

logger = logging.getLogger(__name__)


async def ensure_time_orders(
    db: AsyncSession,
    project_id,
    client: LLMClient,
    llm_failures: list[str] | None = None,
) -> dict[str, int]:
    """Return raw scene_time → semantic order for the project.

    New values are sent to the LLM once (incremental append); everything
    known lives in the cache afterwards. Caller commits.
    """
    pid = _convert(project_id)

    scene_times = await db.execute(
        select(Scene.scene_time).where(
            Scene.project_id == pid,
            Scene.scene_time.isnot(None),
            Scene.scene_time != "",
        )
    )
    raw_all = sorted({r[0] for r in scene_times.all()})
    if not raw_all:
        return {}

    cached_rows = await db.execute(
        select(ConsistencyTimeCache.raw, ConsistencyTimeCache.order_seq).where(
            ConsistencyTimeCache.project_id == pid
        )
    )
    cache = {raw: seq for raw, seq in cached_rows.all()}

    new_values = [v for v in raw_all if v not in cache]
    if new_values:
        payload = await llm_json(
            client,
            prompts.TIME_SYSTEM_PROMPT,
            prompts.build_time_normalize_prompt(raw_all),
            max_tokens=2048,
            reasoning_effort="low",
            temperature=0.0,
            timeout=30.0,
            on_failure=lambda d: (llm_failures is not None and llm_failures.append(d))
            or None,
        )
        if payload:
            for item in payload.get("order") or []:
                if not isinstance(item, dict):
                    continue
                raw = str(item.get("raw", "")).strip()
                seq = int(item.get("order_seq", 0))
                if raw and seq >= 0 and raw not in cache:
                    stmt = pg_insert(ConsistencyTimeCache).values(
                        project_id=pid, raw=raw, order_seq=seq
                    ).on_conflict_do_nothing(
                        index_elements=["project_id", "raw"]
                    )
                    await db.execute(stmt)
                    cache[raw] = seq

    return cache


def _convert(project_id) -> uuid.UUID:
    return project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(project_id)