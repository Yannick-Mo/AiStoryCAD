"""Consistency checker v3 — the orchestrator of the check path (§6, §14.3).

The check never extracts facts and never reads the whole book. It runs:

  stage 0  structural rules            (pure code, 0 token)
  stage 1  hash reconcile → wait queue drain → candidate reconcile
  stage 2  judge pending candidates      (batched, concurrent, small context)
  stage 3  timeline (time cache + code)
  stage 4  global review (fact projection, not full text)
  stage 5  assemble + persist + ONE commit (§14.5 — no half reports)

``ConsistencyChecker.check_all`` keeps the v2 signature so REST / MCP /
agent-tool callers are unchanged. ``live_hint`` backs §9.2's inline hint.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency import prompts
from app.agent.consistency.models import (
    CandidateView,
    ConsistencyIssue,
    ConsistencyReport,
    Verdict,
)
from app.agent.consistency.orm import ConflictCandidateRecord, ConsistencyFact
from app.agent.consistency.persistence import persist_report
from app.agent.consistency.reconcile import (
    project_queue_depth,
    reconcile_project,
    reconcile_project_hash,
)
from app.agent.consistency.rules import run_structural_rules
from app.agent.consistency.time_cache import ensure_time_orders
from app.agent.consistency.utils import estimate_tokens, llm_json
from app.agent.consistency.worker import get_worker, read_live_hints
from app.config import settings as default_settings
from app.llm.client import LLMClient, get_shared_client
from app.project.models import Project
from app.storycad.models import Chapter, Character, Scene

ProgressCb = Callable[[str, int, int, str], Awaitable[None]]

_TIMELINE_ATTRS = {"时间标签", "事件名", "相对顺序", "结果"}
_WORLD_ATTRS = {"规则", "效果", "代价", "物品", "功能", "材质", "来源", "能力"}
_HARD_ATTRS = {"瞳色", "发色", "身份", "能力"}
_QUEUE_DRAIN_WAIT_S = 30.0
_QUEUE_DRAIN_POLL_S = 2.0


def _candidate_issue_severity(attribute: str) -> str:
    """§6 阶段2: 设定/事实类硬规范 → error,其余 → warning."""
    if attribute in _WORLD_ATTRS or attribute in _HARD_ATTRS:
        return "error"
    return "warning"


def _candidate_view(cand: ConflictCandidateRecord) -> CandidateView:
    return CandidateView(
        id=str(cand.id),
        entity=cand.entity,
        attribute=cand.attribute,
        value_a=cand.value_a,
        value_b=cand.value_b,
        evidence_a=cand.evidence_a or "",
        evidence_b=cand.evidence_b or "",
        scene_a=str(cand.scene_a) if cand.scene_a else None,
        scene_b=str(cand.scene_b) if cand.scene_b else None,
        chapter_a=str(cand.chapter_a) if cand.chapter_a else None,
        chapter_b=str(cand.chapter_b) if cand.chapter_b else None,
    )


class ConsistencyChecker:
    """Entry point of the check path (REST / MCP / agent tools)."""

    def __init__(
        self,
        db: AsyncSession,
        llm_client: LLMClient | None = None,
        settings=default_settings,
    ):
        self.db = db
        self._settings = settings
        self._llm_client = llm_client or get_shared_client().fork()
        self._llm_failures = 0
        self._llm_failure_sample = ""

    # ------------------------------------------------------------------
    # LLM accounting
    # ------------------------------------------------------------------

    async def _progress(
        self, stage: str, done: int, total: int, message: str = "", progress_cb=None
    ) -> None:
        if progress_cb is None:
            return
        try:
            await progress_cb(stage, done, total, message)
        except Exception:
            logger.debug("progress callback failed", exc_info=True)

    def _record_failure(self, detail: str) -> None:
        self._llm_failures += 1
        if not self._llm_failure_sample:
            self._llm_failure_sample = (detail or "")[:300]

    async def _llm_json(
        self, system: str, user: str, *, max_tokens: int,
        reasoning_effort: str, temperature: float, timeout: float = 90.0,
    ) -> dict | None:
        return await llm_json(
            self._llm_client, system, user,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            timeout=timeout,
            on_failure=self._record_failure,
        )

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def check_all(
        self,
        project_id: str,
        requested_by: str | None = None,
        progress_cb: ProgressCb | None = None,
    ) -> ConsistencyReport:
        started = time.monotonic()
        pid = uuid.UUID(project_id)

        #
        # 检查前:hash 对账 → 等待本项目队列排空 (§6 阶段1 前置)
        #
        drift = 0
        stale_scenes = 0
        try:
            stats_h = await reconcile_project_hash(self.db, pid, settings=self._settings)
            drift = stats_h.get("drift", 0)
        except Exception:
            logger.warning("check-time hash reconcile failed", exc_info=True)
        await self._progress("reconcile", 0, 1, "哈希对账完成", progress_cb)

        worker = get_worker()
        if drift or worker is not None:
            waited = 0.0
            while waited < _QUEUE_DRAIN_WAIT_S:
                depth = await project_queue_depth(self.db, pid)
                if depth == 0:
                    break
                await asyncio.sleep(_QUEUE_DRAIN_POLL_S)
                waited += _QUEUE_DRAIN_POLL_S
            stale_scenes = await project_queue_depth(self.db, pid)
            if stale_scenes:
                logger.warning("check proceeding with %d stale scenes", stale_scenes)
        await self._progress("reconcile", 1, 1, "队列已排空" if not stale_scenes else "队列超时(基于陈旧场景)", progress_cb)

        #
        # ---- 阶段 0:结构规则(纯代码) + 阶段 1:候选核对(纯 SQL) ----
        #
        characters = await _load_all(self.db, Character, pid)
        chapters = await _load_all(self.db, Chapter, pid)
        scenes = await _load_all(self.db, Scene, pid)
        project_row = await self.db.execute(select(Project).where(Project.id == pid))
        project = project_row.scalar_one_or_none()
        global_settings = (project.global_settings or "") if project else ""
        rules_issues = run_structural_rules(global_settings, characters, chapters, scenes)
        await self._progress("structural", 1, 1, "结构规则检查完成", progress_cb)

        re_stats = await reconcile_project(self.db, pid, settings=self._settings)
        await self._progress("reconcile", 2, 2, "候选核对完成", progress_cb)

        #
        # ---- 阶段 2:判定(LLM, 只对有候选时花钱) ----
        #
        judge_issues = await self._judge_stage(pid, progress_cb=progress_cb)

        #
        # ---- 阶段 3:时间线(缓存 + 代码) ----
        #
        timeline_issues = await self._timeline_stage(pid, progress_cb=progress_cb)

        #
        # ---- 阶段 4:全局审查(事实投影) ----
        #
        global_issues = await self._global_stage(pid, progress_cb=progress_cb)

        #
        # ---- 阶段 5:组装 + 落库(must commit — 修 v2 P0) ----
        #
        all_issues = rules_issues + judge_issues + timeline_issues + global_issues
        report = ConsistencyReport(project_id=str(pid), issues=all_issues)
        report.finalize(str(pid))

        await self._progress("assemble", 0, 1, "报告组装", progress_cb)
        meta = {
            "mode": "ledger",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "tokens_estimated": sum(estimate_tokens(i.description or "") for i in all_issues),
            "drift_enqueued": drift,
            "stale_scenes": stale_scenes,
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
            "llm_failures": self._llm_failures,
            "llm_failure_sample": self._llm_failure_sample,
            "candidate_stats": re_stats,
            "stages": {
                "rules": len(rules_issues),
                "judge": len(judge_issues),
                "timeline": len(timeline_issues),
                "global": len(global_issues),
            },
        }
        report.meta = meta
        if self._llm_failures:
            report.summary = (
                f"{report.summary}（有 {self._llm_failures} 次 LLM 调用失败，"
                f"结果为「未发现一致性问题」时仍可能不完整，请检查 API Key/余额）"
            )
        try:
            await persist_report(self.db, pid, requested_by, report, meta)
            await self.db.commit()
        except Exception:
            logger.warning("failed to persist consistency report", exc_info=True)
            await self.db.rollback()
        await self._progress("assemble", 1, 1, "报告生成完成", progress_cb)
        return report

    # ------------------------------------------------------------------
    # 阶段 2 — 判定
    # ------------------------------------------------------------------

    async def _judge_stage(self, pid: uuid.UUID, progress_cb=None) -> list[ConsistencyIssue]:
        """Judge pending candidates (§6 阶段2), batched and concurrent."""
        result = await self.db.execute(
            select(ConflictCandidateRecord)
            .where(
                ConflictCandidateRecord.project_id == pid,
                ConflictCandidateRecord.status == "pending",
            )
            .order_by(ConflictCandidateRecord.last_seen_at)
        )
        pending = list(result.scalars().all())
        if not pending:
            return []

        issues: list[ConsistencyIssue] = []
        batch_size = self._settings.consistency_judge_batch
        sem = asyncio.Semaphore(self._settings.consistency_max_concurrency)
        done = 0
        await self._progress("judge", 0, len(pending), "候选判定", progress_cb)

        async def _judge_batch(batch) -> None:
            nonlocal done
            async with sem:
                views = [_candidate_view(c) for c in batch]
                context = await self._setting_context(pid, [v.entity for v in views])
                payload = await self._llm_json(
                    prompts.JUDGE_SYSTEM_PROMPT,
                    prompts.build_judge_batch_prompt(views, context),
                    max_tokens=4096,
                    reasoning_effort="medium",
                    temperature=0.2,
                    timeout=self._settings.consistency_judge_timeout_s,
                )
                if payload is None:
                    # 判定失败(LLM 异常/超时):候选保持 pending,下次重判。
                    self._record_failure("候选判定批次无响应，候选保持 pending 待下次重判")
                    done += len(batch)
                    await self._progress("judge", done, len(pending), "候选判定中(批次失败)", progress_cb)
                    return
                verdicts = self._parse_judge_verdicts(payload, len(batch))
                for cand, v in zip(batch, verdicts):
                    cand.status = "verified"
                    cand.verdict = v["verdict"].value
                    cand.severity = v["severity"]
                    cand.explanation = v["explanation"]
                    issued = self._candidate_issues(cand)
                    for issue in issued:
                        issue.candidate_id = str(cand.id)
                    issues.extend(issued)
                done += len(batch)
                await self._progress("judge", done, len(pending), "候选判定中", progress_cb)

        for start in range(0, len(pending), batch_size):
            await _judge_batch(pending[start : start + batch_size])
        await self._progress("judge", len(pending), len(pending), "候选判定完成", progress_cb)
        return issues

    @staticmethod
    def _candidate_issues(cand: ConflictCandidateRecord) -> list[ConsistencyIssue]:
        if cand.verdict == Verdict.REAL_INCONSISTENCY.value:
            severity = cand.severity or _candidate_issue_severity(cand.attribute)
            return [
                ConsistencyIssue(
                    check_type="character",
                    severity=severity,
                    entity_type="character",
                    entity_id=str(cand.scene_a) if cand.scene_a else None,
                    description=(
                        f"实体「{cand.entity}」的「{cand.attribute}」前后不一致："
                        f"「{cand.value_a}」 vs 「{cand.value_b}」"
                    ),
                    suggestion=f"请统一「{cand.entity}」的「{cand.attribute}」，并检查相关场景的叙述",
                    chapter_id=str(cand.chapter_a) if cand.chapter_a else None,
                    scene_id=str(cand.scene_a) if cand.scene_a else None,
                    verdict=Verdict.REAL_INCONSISTENCY,
                    evidence=[e for e in (cand.evidence_a, cand.evidence_b) if e],
                )
            ]
        if cand.verdict == Verdict.NEEDS_REVIEW.value:
            return [
                ConsistencyIssue(
                    check_type="judge",
                    severity="info",
                    entity_type="character",
                    entity_id=str(cand.scene_a) if cand.scene_a else None,
                    description=(
                        f"「{cand.entity}」的「{cand.attribute}」出现不同描述"
                        f"（「{cand.value_a}」 vs 「{cand.value_b}」），建议人工复核"
                    ),
                    suggestion="请人工确认是否有意为之（成长/伪装/闪回/修辞等）",
                    verdict=Verdict.NEEDS_REVIEW,
                    evidence=[e for e in (cand.evidence_a, cand.evidence_b) if e],
                )
            ]
        return []

    def _parse_judge_verdicts(self, payload: dict | None, n: int) -> list[dict]:
        """One batch payload → per-candidate verdict dicts. 判定缺失/坏响应 → needs_review."""
        out: list[dict] = []
        raw = (payload or {}).get("verdicts") or []
        for idx in range(n):
            entry = next((v for v in raw if isinstance(v, dict) and v.get("index") == idx), None)
            if entry is None:
                out.append({"verdict": Verdict.NEEDS_REVIEW, "severity": "info", "explanation": "判定缺失"})
                continue
            try:
                verdict = Verdict(str(entry.get("verdict", "")))
            except ValueError:
                verdict = Verdict.NEEDS_REVIEW
            severity = str(entry.get("severity", "warning"))
            if severity not in ("error", "warning", "info"):
                severity = "warning"
            out.append({
                "verdict": verdict,
                "severity": severity,
                "explanation": str(entry.get("explanation", ""))[:500],
            })
        return out

    async def _setting_context(self, pid: uuid.UUID, entities: list[str]) -> str:
        """相关设定:该实体在 Character 档案 + global_settings 的字段,截断 ≤800 字(§6 阶段2)。"""
        if not entities:
            return ""
        entity_set = set(entities)
        result = await self.db.execute(
            select(Character).where(
                Character.project_id == pid,
                Character.name.in_(entity_set),
            )
        )
        char_rows = result.scalars().all()
        project_row = await self.db.execute(select(Project).where(Project.id == pid))
        project = project_row.scalar_one_or_none()
        parts: list[str] = []
        for c in char_rows:
            fields = []
            if c.appearance:
                fields.append(f"外貌={c.appearance}")
            if c.personality:
                fields.append(f"性格={c.personality}")
            if c.background:
                fields.append(f"背景={c.background}")
            if fields:
                parts.append(f"{c.name}: {'，'.join(fields)}")
        if project and project.global_settings:
            parts.append(f"世界观: {project.global_settings}")
        text = "\n".join(parts)
        return text[: self._settings.consistency_setting_context_chars]

    # ------------------------------------------------------------------
    # 阶段 3 — 时间线(代码 + 缓存)
    # ------------------------------------------------------------------

    async def _timeline_stage(self, pid: uuid.UUID, progress_cb=None) -> list[ConsistencyIssue]:
        failures: list[str] = []
        orders = await ensure_time_orders(
            self.db, pid, self._llm_client, llm_failures=failures
        )
        if failures:
            self._llm_failures += len(failures)
            if not self._llm_failure_sample:
                self._llm_failure_sample = failures[0][:300]
        if not orders:
            return []
        chapters = await _load_all(self.db, Chapter, pid)
        scenes = await _load_all(self.db, Scene, pid)
        chapter_titles = {c["id"]: c.get("title", "") for c in chapters}
        issues: list[ConsistencyIssue] = []
        max_seen = -1
        for s in scenes:
            raw = (s.get("scene_time") or "").strip()
            seq = orders.get(raw)
            if seq is None:
                continue
            if seq < max_seen:
                issues.append(
                    ConsistencyIssue(
                        check_type="timeline",
                        severity="warning",
                        entity_type="scene",
                        entity_id=s.get("id"),
                        description=(
                            f"场景「{s.get('title') or s.get('id')}」的时间标签「{raw}」"
                            f"与前文时序逆行（前文最大语义序 {max_seen}，此处 {seq}）"
                        ),
                        suggestion="请修正场景的时间标签或排序",
                        chapter_id=s.get("chapter_id"),
                        scene_id=s.get("id"),
                    )
                )
            max_seen = max(max_seen, seq)
        return issues

    # ------------------------------------------------------------------
    # 阶段 4 — 全局审查(事实投影)
    # ------------------------------------------------------------------

    async def _global_stage(self, pid: uuid.UUID, progress_cb=None) -> list[ConsistencyIssue]:
        rows_result = await self.db.execute(
            select(
                ConsistencyFact.entity,
                ConsistencyFact.attribute,
                ConsistencyFact.value,
                ConsistencyFact.evidence,
                ConsistencyFact.chapter_id,
            )
            .where(ConsistencyFact.project_id == pid, ConsistencyFact.is_active)
            .order_by(ConsistencyFact.created_at)
        )
        rows = rows_result.all()
        if not rows:
            return []
        await self._progress("global", 0, 1, "全局事实投影", progress_cb)

        projection_lines = [
            f"实体={e} 属性={a} 值={v} 引文={(ev or '')[:80]} 章节={str(ch or '')[:8]}"
            for e, a, v, ev, ch in rows
        ]
        estimated = len("".join(projection_lines))
        chunks = [projection_lines]
        if estimated > self._settings.consistency_global_projection_cap:
            mid = max(1, len(projection_lines) // 2)
            chunks = [projection_lines[:mid], projection_lines[mid:]]

        issued: list[ConsistencyIssue] = []
        for chunk in chunks:
            if not chunk:
                continue
            payload = await self._llm_json(
                prompts.GLOBAL_PROJECTION_SYSTEM_PROMPT,
                prompts.build_global_projection_prompt("\n".join(chunk)),
                max_tokens=2048,
                reasoning_effort="medium",
                temperature=0.4,
                timeout=60.0,
            )
            issued.extend(self._parse_projection_payload(payload))
        await self._progress("global", 1, 1, "全局审查完成", progress_cb)

        seen: set[tuple] = set()
        out: list[ConsistencyIssue] = []
        for i in issued:
            key = (i.check_type, i.severity, i.entity_type, i.description[:60])
            if key in seen:
                continue
            seen.add(key)
            out.append(i)
        return out

    @staticmethod
    def _parse_projection_payload(payload: dict | None) -> list[ConsistencyIssue]:
        if not payload:
            return []
        issues: list[ConsistencyIssue] = []
        for item in payload.get("issues") or []:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity", "info"))
            if severity not in ("error", "warning", "info"):
                severity = "info"
            desc = str(item.get("description", "")).strip()
            if not desc:
                continue
            issues.append(
                ConsistencyIssue(
                    check_type="global",
                    severity=severity,
                    entity_type=str(item.get("entity") or item.get("entity_type") or "chapter"),
                    entity_id=None,
                    description=desc,
                    suggestion=str(item.get("suggestion")) if item.get("suggestion") else None,
                    verdict=None,
                    evidence=[str(item.get("evidence", ""))[:80]] if item.get("evidence") else None,
                )
            )
        return issues

    # ------------------------------------------------------------------
    # §9.2 — 编辑期间内联提示
    # ------------------------------------------------------------------

    async def live_hint(
        self, project_id: str, scene_id: str, last_saved_at=None
    ) -> list[dict]:
        hits = read_live_hints(str(project_id), str(scene_id), last_saved_at)
        for h in hits:
            h.setdefault("id", f"{h['entity']}:{h['attribute']}:{h['value_a']}:{h['value_b']}")
        return hits


async def _load_all(db: AsyncSession, model, pid: uuid.UUID) -> list[dict]:
    result = await db.execute(
        select(model)
        .where(model.project_id == pid)
        .order_by(model.sort_order)
    )
    from app.utils import row_to_dict

    return [row_to_dict(x) for x in result.scalars().all()]