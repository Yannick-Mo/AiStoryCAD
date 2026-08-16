"""Write-path background worker for the consistency v3 ledger (§5.3).

Everything that turns *content writes* into *ledger rows* lives here:

  * :class:`Inbox`      — bounded in-memory event inbox (sync puts from ORM
    events, async drain); overflow drops silently with a counter.
  * :class:`FactWorker` — consumes the inbox + the persistent queue and runs
    the per-scene pipeline (extract → normalise → vectorise → insert →
    probe) inside ONE transaction. Retries/backoff live here. It never
    judges — judging belongs to the checker.
  * live probes        — write-time probe hits are kept in-memory (§9.2)
    and served by ``ConsistencyChecker.live_hint``.

The ORM event registration lives in ``app/events/consistency_events.py`` so
the model layer stays free of consistency imports.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.consistency import prompts
from app.agent.consistency.facts import (
    chunk_text,
    dedup_facts,
    facts_from_extraction,
    find_cluster_candidates,
    insert_facts_for_scene,
    normalise_value,
)
from app.agent.consistency.models import SourceType
from app.agent.consistency.orm import FactQueueItem
from app.agent.consistency.utils import hash_content, llm_json
from app.config import settings as default_settings
from app.llm.client import LLMClient, get_shared_client
from app.storycad.models import Scene, SceneContent

logger = logging.getLogger(__name__)

_MAX_BACKOFF_MINUTES = 60  # cap for exponential backoff (1/2/4/8…min)
_DEAD_RETRY_LIMIT = 5  # failed attempts before a queue row goes terminal (dead)
_AUDIT_BATCH_SIZE = 500  # keyset-paginated scan batch (bounded memory)
_AUDIT_MAX_SCENES = 20000  # hard cap per audit run


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


class Inbox:
    """Bounded in-memory inbox for ORM write events (§5.2).

    ``put`` is synchronous (event handlers cannot await); ``drain`` is async.
    When full, new items are dropped and counted — the save transaction must
    never be blocked, delayed or failed by the consistency pipeline.
    """

    def __init__(self, maxsize: int = 2000):
        self._items: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        self.maxsize = maxsize
        self.dropped = 0

    def put(self, project_id, scene_id, content_hash: str) -> None:
        if len(self._items) >= self.maxsize:
            self.dropped += 1
            return
        self._items.append((project_id, scene_id, content_hash))

    async def drain(self, max_items: int = 64) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
        if not self._items:
            return []
        items, self._items = self._items[:max_items], self._items[max_items:]
        return items


# ---------------------------------------------------------------------------
# Live probe hints (edit-time inline hints, in-memory per §9.2)
# ---------------------------------------------------------------------------

_live_hints: dict[tuple[str, str], list[dict]] = {}
_live_lock = asyncio.Lock()
# Hard cap on distinct (project, scene) keys — evict oldest when exceeded
# (plain dicts preserve insertion order, so next(iter(...)) is the oldest).
_LIVE_HINTS_MAX_KEYS = 5000


async def push_live_hint(
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
    *,
    entity: str,
    attribute: str,
    value_a: str,
    value_b: str,
    evidence_a: str,
    evidence_b: str,
    scene_b: str,
    chapter_a: str,
    chapter_b: str,
) -> None:
    key = (str(project_id), str(scene_id))
    dedupe_key = (entity, attribute, value_a, value_b)

    async with _live_lock:
        hits = _live_hints.setdefault(key, [])
        if any((h["entity"], h["attribute"], h["value_a"], h["value_b"]) == dedupe_key for h in hits):
            return
        hits.append(
            {
                "entity": entity,
                "attribute": attribute,
                "value_a": value_a,
                "value_b": value_b,
                "evidence_a": (evidence_a or "")[:80],
                "evidence_b": (evidence_b or "")[:80],
                "scene_a": str(scene_id),
                "scene_b": scene_b,
                "chapter_a": chapter_a or "",
                "chapter_b": chapter_b or "",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(hits) > 100:
            del hits[:-100]
        while len(_live_hints) > _LIVE_HINTS_MAX_KEYS:
            del _live_hints[next(iter(_live_hints))]


def read_live_hints(
    project_id: str, scene_id: str, since: datetime | None
) -> list[dict]:
    """Return probe hits for the scene created after *since* (§9.2)."""
    hits = _live_hints.get((project_id, scene_id), [])
    if since is None:
        return list(hits)
    return [h for h in hits if h["created_at"] > since.isoformat()]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class FactWorker:
    """The background consumer of write events → ledger rows (§5.3)."""

    def __init__(
        self,
        inbox: Inbox,
        session_factory: async_sessionmaker,
        client: LLMClient | None = None,
        settings=default_settings,
    ):
        self.inbox = inbox
        self.session_factory = session_factory
        self._client = client or get_shared_client().fork()
        self._settings = settings
        self._running = False
        self._extract_sem = asyncio.Semaphore(settings.consistency_max_concurrency)
        self.stats = {"processed": 0, "skipped": 0, "failed": 0, "audited": 0}

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def process_one(self, project_id, scene_id, content_hash: str) -> None:
        """Single-scene pipeline. One transaction; retry/backoff on failure."""
        async with self.session_factory() as db:
            try:
                await self._process_one_inner(db, project_id, scene_id, content_hash)
                self.stats["processed"] += 1
            except _SceneDeleted:
                await db.rollback()
                await self._mark_done_after_deleted(db, project_id, scene_id)
                logger.warning("scene %s no longer exists; queue row done", scene_id)
            except _ExtractionFailed as exc:
                await db.rollback()
                await self._mark_failed(db, project_id, scene_id, exc, content_hash)
                self.stats["failed"] += 1
            except Exception as exc:
                # Special case: FK violation because the scene was deleted
                # mid-extraction (§5.1) — swallow, mark done, don't retry.
                if "foreign key" in str(exc).lower() or "integrityerror" in str(exc).lower():
                    await db.rollback()
                    await self._mark_done_after_deleted(db, project_id, scene_id)
                    logger.warning("scene %s deleted mid-extraction; queue row done", scene_id)
                else:
                    await db.rollback()
                    await self._mark_failed(db, project_id, scene_id, exc, content_hash)
                    self.stats["failed"] += 1
                    logger.warning("worker failed scene=%s: %s", scene_id, exc)

    async def run_forever(self, poll: float = 0.5) -> None:
        """Main loop: drain the inbox, then pick up eligible queue rows."""
        self._running = True
        try:
            while self._running:
                try:
                    await self._tick()
                except Exception:
                    logger.exception("worker tick failed")
                await asyncio.sleep(poll)
        finally:
            self._running = False

    async def stop(self) -> None:
        self._running = False

    async def _tick(self) -> None:
        batch = await self.inbox.drain()
        for pid, sid, h in batch:
            try:
                await asyncio.wait_for(self.process_one(pid, sid, h), timeout=600)
            except Exception:
                logger.exception("worker process_one crashed pid=%s sid=%s", pid, sid)
        # Persistent-queue backlog (e.g. retries waiting on next_retry_at).
        await self._process_backlog()

    async def _process_backlog(self, limit: int = 32) -> None:
        async with self.session_factory() as db:
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(FactQueueItem)
                .where(
                    FactQueueItem.status.in_(("pending", "failed")),
                    FactQueueItem.next_retry_at <= now,
                )
                .order_by(FactQueueItem.updated_at)
                .limit(limit)
            )
            rows = result.scalars().all()
        for row in rows:
            await self.process_one(row.project_id, row.scene_id, row.content_hash)

    async def audit_now(self, session_factory=None) -> int:
        """Periodic hash audit — bootstrap/sweep the queue (§5.1 兜底 A, §11.2).

        Scans ``scene_contents``, enqueues scenes whose content hash differs
        from the queue row (or that have no queue row), and clears queue rows
        whose scene no longer exists. Returns items enqueued.

        The scan is keyset-paginated (``scene_id`` cursors) so the full table
        is never loaded into memory at once; the run stops at
        ``_AUDIT_MAX_SCENES`` scenes.
        """
        factory = session_factory or self.session_factory
        async with factory() as db:
            now = datetime.now(timezone.utc)
            seen: dict[tuple, str] = {}
            enqueued = 0
            last_id = None
            while len(seen) < _AUDIT_MAX_SCENES:
                stmt = select(
                    SceneContent.scene_id, SceneContent.project_id, SceneContent.content
                )
                if last_id is not None:
                    stmt = stmt.where(SceneContent.scene_id > last_id)
                rows = (
                    await db.execute(
                        stmt.order_by(SceneContent.scene_id).limit(_AUDIT_BATCH_SIZE)
                    )
                ).all()
                if not rows:
                    break
                for scene_id, project_id, content in rows:
                    if len(seen) >= _AUDIT_MAX_SCENES:
                        break
                    h = hash_content(content or "")
                    seen[(str(project_id), str(scene_id))] = h
                    if await self._enqueue(db, project_id, scene_id, h):
                        enqueued += 1
                    last_id = scene_id
                await asyncio.sleep(0)  # yield so other tasks make progress
            if len(seen) >= _AUDIT_MAX_SCENES:
                logger.warning(
                    "audit hit cap of %d scenes; run truncated (more scenes to scan)",
                    _AUDIT_MAX_SCENES,
                )
            # Queue rows whose scenes are gone — clear them (§5.1/5.4).
            orphan = await db.execute(
                select(FactQueueItem.scene_id)
                .outerjoin(Scene, Scene.id == FactQueueItem.scene_id)
                .where(Scene.id.is_(None))
            )
            for (orphan_sid,) in orphan:
                await db.execute(
                    sa_delete(FactQueueItem).where(FactQueueItem.scene_id == orphan_sid)
                )
            # Stale 'processing' from a crashed run — reset to pending.
            await db.execute(
                update(FactQueueItem)
                .where(
                    FactQueueItem.status == "processing",
                    FactQueueItem.updated_at < now - timedelta(minutes=5),
                )
                .values(status="pending", next_retry_at=now)
            )
            await db.commit()
            self.stats["audited"] += 1
            return enqueued

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _enqueue(self, db: AsyncSession, project_id, scene_id, content_hash: str) -> bool:
        """Persist the queue row if the hash differs (hash gating).

        Returns True when the caller should proceed with the pipeline. A row
        that is already ``done`` with this exact hash → False (nothing to do).
        Failed rows with the same hash are *retries*: they only proceed once
        their backoff (``next_retry_at``) has elapsed; ``dead`` rows never
        proceed — only a content change resets the retry state.
        """
        row = await db.get(
            FactQueueItem,
            (project_id, scene_id),
            populate_existing=True,
        )
        if row is not None:
            return await self._gate_existing_row(db, row, content_hash)
        try:
            db.add(
                FactQueueItem(
                    project_id=project_id,
                    scene_id=scene_id,
                    content_hash=content_hash,
                    status="pending",
                    next_retry_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()
        except IntegrityError:
            await db.rollback()
            row = await db.get(FactQueueItem, (project_id, scene_id))
            if row is not None:
                return await self._gate_existing_row(db, row, content_hash)
            raise
        return True

    async def _gate_existing_row(
        self, db: AsyncSession, row: FactQueueItem, content_hash: str
    ) -> bool:
        """Same-hash gating; reset retry state only when the content changed."""
        if row.content_hash == content_hash:
            if row.status in ("pending", "processing"):
                return True  # earlier attempt in flight or awaiting retry
            if row.status == "done":
                return False
            if row.status == "dead":
                return False  # retries exhausted — only a content change revives
            # failed: honour the backoff schedule instead of resetting it.
            if row.next_retry_at <= datetime.now(timezone.utc):
                row.status = "pending"
                row.next_retry_at = datetime.now(timezone.utc)
                await db.flush()
                return True
            return False
        self._reset_for_new_content(row, content_hash)
        await db.flush()
        return True

    @staticmethod
    def _reset_for_new_content(row: FactQueueItem, content_hash: str) -> None:
        row.content_hash = content_hash
        row.status = "pending"
        row.next_retry_at = datetime.now(timezone.utc)
        row.retry_count = 0
        row.last_error = None

    async def _mark_done_after_deleted(self, db: AsyncSession, project_id, scene_id) -> None:
        row = await db.get(FactQueueItem, (project_id, scene_id))
        if row is None:
            return
        row.status = "done"
        row.updated_at = datetime.now(timezone.utc)
        await db.commit()

    async def _mark_done(self, db: AsyncSession, project_id, scene_id, content_hash: str) -> None:
        row = await db.get(FactQueueItem, (project_id, scene_id))
        if row is None:
            return
        row.status = "done"
        row.content_hash = content_hash
        row.updated_at = datetime.now(timezone.utc)
        await db.commit()

    async def _mark_failed(self, db: AsyncSession, project_id, scene_id, exc: Exception, content_hash: str) -> None:
        row = await db.get(FactQueueItem, (project_id, scene_id))
        if row is None:
            try:
                row = FactQueueItem(
                    project_id=project_id,
                    scene_id=scene_id,
                    content_hash=content_hash,
                    status="failed",
                    last_error=str(exc)[:500],
                )
                db.add(row)
                await db.flush()
            except IntegrityError:
                await db.rollback()
                row = await db.get(FactQueueItem, (project_id, scene_id))
                if row is None:
                    raise
        backoff_min = min(60, 2 ** min(row.retry_count, 6))
        row.retry_count += 1
        row.last_error = str(exc)[:500]
        if row.retry_count >= _DEAD_RETRY_LIMIT:
            # 重试耗尽 → 终态 dead:audit/backlog 都不会再触碰,除非内容变化。
            row.status = "dead"
        else:
            row.status = "failed"
            row.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=backoff_min)
        await db.commit()

    async def _process_one_inner(
        self, db: AsyncSession, project_id, scene_id, content_hash: str
    ) -> None:
        # Cross-process guard: never double-process the same project.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
            {"k": f"consistency:{project_id}"},
        )
        from app.storycad.models import Chapter, Scene, SceneContent

        scene = await db.get(Scene, scene_id)
        if scene is None or scene.project_id != project_id:
            raise _SceneDeleted()

        content_row = await db.get(SceneContent, scene_id)
        content = (content_row.content if content_row else "") or ""
        actual_hash = hash_content(content)
        if actual_hash != content_hash:
            content_hash = actual_hash  # event raced; re-gate on reality

        if not await self._enqueue(db, project_id, scene_id, content_hash):
            # Same content already processed — nothing to do.
            await db.commit()
            self.stats["skipped"] += 1
            return
        queue = await db.get(FactQueueItem, (project_id, scene_id))
        queue.status = "processing"
        queue.updated_at = datetime.now(timezone.utc)
        await db.flush()

        chapter = await db.get(Chapter, scene.chapter_id) if scene.chapter_id else None
        scene_dict = {
            "id": str(scene.id),
            "project_id": str(project_id),
            "chapter_id": str(scene.chapter_id) if scene.chapter_id else None,
            "title": scene.title or "",
            "pov_character": scene.pov_character or "",
            "setting": scene.setting or "",
            "scene_time": scene.scene_time or "",
            "summary": scene.summary or "",
        }

        rows: list[dict] = []
        if content.strip() == "":
            # 清空语义 (§5.3 step 2): no LLM, only invalidation.
            await self._insert_and_probe(db, project_id, scene_id, scene_dict, rows)
            await self._mark_done_inline(db, project_id, scene_id, content_hash)
            await db.commit()
            return

        if len(content) <= self._settings.consistency_skip_small_scene_chars:
            rows = self._scene_meta_rows(scene_dict)
        else:
            rows = await self._extract_rows(db, scene_dict, chapter, content)
            if rows is None:
                raise _ExtractionFailed(str(scene_id)[:8] + " 提取失败")

        await self._insert_and_probe(db, project_id, scene_id, scene_dict, rows)
        await self._mark_done_inline(db, project_id, scene_id, content_hash)
        await db.commit()

    async def _insert_and_probe(
        self, db: AsyncSession, project_id, scene_id, scene_dict: dict, rows: list[dict]
    ) -> None:
        """Step 3–6 of §5.3: normalise → insert → write-time probe."""
        result = await insert_facts_for_scene(
            db,
            project_id=project_id,
            scene_id=scene_id,
            chapter_id=scene_dict.get("chapter_id"),
            rows=rows,
        )
        if rows:
            await self._probe_new_facts(db, project_id, scene_id, rows)
        await db.flush()
        logger.debug("ledger: %s upserted, %s deactivated", result["inserted"], result["deactivated"])

    async def _mark_done_inline(self, db: AsyncSession, project_id, scene_id, content_hash: str) -> None:
        row = await db.get(FactQueueItem, (project_id, scene_id))
        if row is None:
            return
        row.status = "done"
        row.content_hash = content_hash
        row.updated_at = datetime.now(timezone.utc)

    # -- extraction ---------------------------------------------------------

    async def _extract_rows(
        self, db: AsyncSession, scene: dict, chapter: dict | None, content: str
    ) -> list[dict] | None:
        """Incremental extraction (§5.3 step 2). None → failed (retry).

        The whole scene content is extracted block by block
        (``consistency_block_chars`` per block) so arbitrarily long scenes
        never blow the context window; per-block results are merged with
        ``dedup_facts`` before insertion.
        """
        settings = self._settings
        blocks = chunk_text(content, settings.consistency_block_chars)

        from app.storycad.models import Character

        result = await db.execute(
            select(Character.name)
            .where(Character.project_id == uuid.UUID(scene["project_id"]))
            .order_by(Character.sort_order)
            .limit(settings.consistency_role_list_n)
        )
        char_names = [r[0] for r in result.all()]

        chapter_title = (chapter.title if chapter else None) or ""
        all_facts = []
        for block_index, block in enumerate(blocks):
            prompt = prompts.build_extractor_prompt(
                chapter_title, scene, block, character_names=char_names
            )
            async with self._extract_sem:
                payload = await llm_json(
                    self._client,
                    prompts.EXTRACTOR_SYSTEM_PROMPT,
                    prompt,
                    reasoning_effort="low",
                    temperature=0.0,
                    timeout=settings.consistency_extract_timeout_s,
                )
                facts = facts_from_extraction(
                    payload or {},
                    scene["id"],
                    scene.get("chapter_id"),
                    block_index=block_index,
                    source_type=SourceType.SCENE_CONTENT,
                )
                if not facts and payload is None:
                    # One retry with error feedback.
                    retry_user = prompt + "\n\n注意：上一次输出不是合法JSON。请只输出一个JSON对象，不要包含任何其他文字。"
                    payload = await llm_json(
                        self._client,
                        prompts.EXTRACTOR_SYSTEM_PROMPT,
                        retry_user,
                        reasoning_effort="low",
                        temperature=0.0,
                        timeout=settings.consistency_extract_timeout_s,
                    )
                    facts = facts_from_extraction(
                        payload or {},
                        scene["id"],
                        scene.get("chapter_id"),
                        block_index=block_index,
                        source_type=SourceType.SCENE_CONTENT,
                    )
                if not facts:
                    return None  # 空 = 失败 → old snapshot preserved, backoff
            all_facts.extend(facts)

        rows = [
            {
                "entity": f.entity,
                "attribute": f.attribute,
                "value": f.value,
                "value_norm": normalise_value(f.value),
                "evidence": f.evidence,
                "source_type": f.source_type.value,
            }
            for f in dedup_facts(all_facts)
        ]
        rows.extend(self._scene_meta_rows(scene))
        return rows

    def _scene_meta_rows(self, scene: dict) -> list[dict]:
        rows: list[dict] = []
        for attr, val in (
            ("所在地", scene.get("setting", "")),
            ("时间标签", scene.get("scene_time", "")),
            ("POV", scene.get("pov_character", "")),
        ):
            if not val:
                continue
            rows.append(
                {
                    "entity": scene.get("title", "") or scene.get("id", "") or "",
                    "attribute": attr,
                    "value": val,
                    "value_norm": normalise_value(val),
                    "evidence": f"场景元数据：{attr}={val}",
                    "source_type": "scene_meta",
                }
            )
        return rows

    async def _probe_new_facts(
        self, db: AsyncSession, project_id, scene_id, rows: list[dict]
    ) -> None:
        """Write-time probe (§5.3 step 6): same cluster, different value → hint.

        Scoped to the ``(entity, attribute)`` pairs of the incoming batch so
        the cluster query uses ``ix_consistency_facts_proj_active_ent_attr``
        instead of scanning the whole project ledger.
        """
        pairs = sorted({(r["entity"], r["attribute"]) for r in rows})
        if not pairs:
            return
        try:
            clusters = await find_cluster_candidates(db, project_id, pairs=pairs)
        except Exception:
            return
        per_key: dict[tuple[str, str], dict[str, tuple[str, str, str]]] = {}
        for entity, attribute, norm, evidence, sid, cid in clusters:
            per_key.setdefault((entity, attribute), {}).setdefault(norm, (evidence, sid, cid))
        for r in rows:
            others = per_key.get((r["entity"], r["attribute"]), {})
            for other_norm, (other_ev, other_sid, other_cid) in others.items():
                if other_norm == r["value_norm"] or not other_norm:
                    continue
                await push_live_hint(
                    project_id,
                    scene_id,
                    entity=r["entity"],
                    attribute=r["attribute"],
                    value_a=r["value_norm"],
                    value_b=other_norm,
                    evidence_a=r.get("evidence", ""),
                    evidence_b=other_ev,
                    scene_b=other_sid,
                    chapter_a=r.get("chapter_id") or "",
                    chapter_b=other_cid,
                )
                break  # one hint per new row is enough


class _SceneDeleted(Exception):
    """Marker: scene no longer exists — swallow and mark queue done."""


class _ExtractionFailed(Exception):
    """Marker: LLM extraction failed twice — mark queue failed with backoff."""


_global_worker: "FactWorker | None" = None


def register_worker(worker: "FactWorker") -> None:
    """Bind the app-lifetime worker so checkers can wait for its drain."""
    global _global_worker
    _global_worker = worker


def get_worker() -> "FactWorker | None":
    return _global_worker