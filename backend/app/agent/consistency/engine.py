"""Consistency engine v2 — the Map-Reduce-Verify pipeline.

Overview
--------
  Stage 0  structural rules           (deterministic, no LLM)
  Stage 1  fact extraction            (Map: per-scene, parallel, cached)
  Stage 2  merge / alias resolution   (Reduce: dedup + entity aliasing)
  Stage 3  conflict verification      (Verify: small-context judgement)
  Stage 4  global review              (cross-act, budget-aware)

Design intent (一致性分析引擎v2设计文档 §2): consistency problems are
*discovered* by grouping facts per ``(entity, attribute)`` in code and then
*judged* in an isolated stage, instead of hoping one giant prompt reads the
whole novel. Every LLM call is streaming, bounded by ``max_tokens``, and
wrapped in ``asyncio.wait_for`` so a stuck reasoning model can never hang
the check (see docs/check_consistency超时问题排查记录.md).

The pipeline takes a single ``AsyncSession``; all LLM calls happen between
DB operations, so concurrent extraction never touches the session.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import Awaitable, Callable, Iterable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency import prompts
from app.agent.consistency.facts import (
    chunk_text,
    dedup_facts,
    find_conflicts,
    facts_from_extraction,
    group_by_entity_attribute,
    scene_meta_facts,
)
from app.agent.consistency.models import (
    ConflictCandidate,
    ConsistencyIssue,
    ConsistencyReport,
    Fact,
    SourceType,
    Verdict,
)
from app.agent.consistency.persistence import (
    load_scene_fact_cache,
    persist_report,
    save_scene_fact_cache,
)
from app.agent.consistency.rules import run_structural_rules
from app.config import settings
from app.llm.client import LLMClient, get_shared_client
from app.llm.types import Message
from app.project.models import Project
from app.storycad.models import Act, Chapter, ChapterEdge, Character, Scene, SceneContent
from app.utils import row_to_dict

ProgressCb = Callable[[str, int, int, str], Awaitable[None]]

TIMELINE_ATTRS = {"时间标签", "时间", "事件名", "相对顺序", "结果"}
WORLD_ATTRS = {"规则", "效果", "代价", "物品", "功能", "材质", "来源", "能力"}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate. CJK ≈ 1 token/char, latin ≈ 4 chars/token."""
    if not text:
        return 0
    return max(1, len(text) // 2)


def _parse_json(content: str) -> dict | None:
    """Three-level JSON extraction: raw → fenced → balanced-brace scan."""
    if not content:
        return None
    candidates: list[str] = []
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(content)
    # Balanced-brace scan from the first '{'.
    start = content.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(content[start : i + 1])
                    break
    for cand in candidates:
        if not cand.strip():
            continue
        try:
            data = json.loads(cand.strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


class ConsistencyPipeline:
    """Runs one full consistency check for a project.

    ``progress_cb`` (optional) is awaited with ``(stage, done, total, message)``
    after each phase so long-running checks can be surfaced via jobs/SSE.
    """

    def __init__(
        self,
        db: AsyncSession,
        llm_client: LLMClient | None = None,
        progress_cb: ProgressCb | None = None,
    ):
        self.db = db
        self._llm = llm_client or get_shared_client().fork()
        self._progress_cb = progress_cb
        self._concurrency = settings.consistency_max_concurrency
        self._block_chars = settings.consistency_block_chars
        self._skip_small = settings.consistency_skip_small_scene_chars

    # ------------------------------------------------------------------
    # Progress + small helpers
    # ------------------------------------------------------------------

    async def _progress(self, stage: str, done: int, total: int, message: str = "") -> None:
        if self._progress_cb is not None:
            try:
                await self._progress_cb(stage, done, total, message)
            except Exception:
                logger.debug("progress callback failed", exc_info=True)

    async def _llm_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        reasoning_effort: str,
        temperature: float,
        timeout: float = 90.0,
    ) -> dict | None:
        """One streaming, time-boxed, JSON-returning LLM call.

        ``max_tokens`` is always explicit and must respect the ≤8192 hard
        gate (reasoning and content share the budget on deepseek-v4-flash).
        """
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
        parts: list[str] = []

        async def _collect() -> None:
            async for tok in self._llm.chat_stream_tokens(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
            ):
                parts.append(tok)

        try:
            await asyncio.wait_for(_collect(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("LLM call timed out after %.0fs", timeout)
            return None
        except Exception:
            logger.warning("LLM call failed", exc_info=True)
            return None
        return _parse_json("".join(parts))

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def _load_project(self, pid: uuid.UUID) -> dict:
        result = await self.db.execute(select(Project).where(Project.id == pid))
        proj = result.scalar_one_or_none()
        global_settings = proj.global_settings or "" if proj else ""

        characters = await self._load_all(Character, pid)
        chapters = await self._load_all(Chapter, pid)
        scenes = await self._load_all(Scene, pid)
        acts = await self._load_all(Act, pid)

        edge_result = await self.db.execute(
            select(ChapterEdge).where(ChapterEdge.project_id == pid)
        )
        edges = [row_to_dict(e) for e in edge_result.scalars().all()]

        contents: dict[str, str] = {}
        sid_strs = [s["id"] for s in scenes if isinstance(s.get("id"), str)]
        if sid_strs:
            content_result = await self.db.execute(
                select(SceneContent).where(SceneContent.scene_id.in_([uuid.UUID(x) for x in sid_strs]))
            )
            for sc in content_result.scalars().all():
                contents[str(sc.scene_id)] = sc.content or ""

        chapter_by_id = {c["id"]: c for c in chapters}
        scene_by_id = {s["id"]: s for s in scenes}
        return {
            "global_settings": global_settings,
            "characters": characters,
            "chapters": chapters,
            "scenes": scenes,
            "acts": acts,
            "edges": edges,
            "contents": contents,
            "chapter_by_id": chapter_by_id,
            "scene_by_id": scene_by_id,
        }

    async def _load_all(self, model, pid: uuid.UUID) -> list[dict]:
        result = await self.db.execute(
            select(model).where(model.project_id == pid).order_by(model.sort_order)
        )
        return [row_to_dict(x) for x in result.scalars().all()]

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def run(self, project_id: str, requested_by: str | None = None) -> ConsistencyReport:
        started = time.monotonic()
        pid = uuid.UUID(project_id)
        data = await self._load_project(pid)
        await self._progress("load", 1, 1, "数据加载完成")

        # ---- Stage 0: structural rules (always runs) ----
        await self._progress("rules", 0, 1, "结构规则检查")
        rules_issues = run_structural_rules(
            data["global_settings"], data["characters"], data["chapters"], data["scenes"]
        )
        await self._progress("rules", 1, 1, "结构规则检查完成")

        # ---- Stage 1: fact extraction ----
        scene_facts, cache_stats = await self._extract_stage(data, pid)

        # ---- Stage 2: merge / alias resolution ----
        merged_facts = await self._merge_stage(data, scene_facts)

        # ---- Stage 3: conflict verification ----
        verify_issues = await self._verify_stage(data, merged_facts)

        # ---- Stage 4: global review ----
        global_issues = await self._global_stage(data, scene_facts)

        # ---- Assemble + persist ----
        all_issues = rules_issues + verify_issues + global_issues
        report = ConsistencyReport(project_id=project_id, issues=all_issues)
        report.finalize(project_id)

        token_estimate = sum(_estimate_tokens(i.description or "") for i in all_issues)
        meta = {
            "mode": "pipeline",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "tokens_estimated": token_estimate,
            "cached_scene_count": cache_stats["cached"],
            "extracted_scene_count": cache_stats["extracted"],
            "scene_count": cache_stats["total"],
            "fact_count": len(merged_facts),
            "stages": {
                "rules": len(rules_issues),
                "verify": len(verify_issues),
                "global": len(global_issues),
            },
        }
        report.meta = meta
        try:
            await persist_report(self.db, pid, requested_by, report, meta)
            await self.db.flush()
        except Exception:
            logger.warning("failed to persist consistency report", exc_info=True)
        await self._progress("assemble", 1, 1, "报告生成完成")
        return report

    # ------------------------------------------------------------------
    # Stage 1 — extraction (Map)
    # ------------------------------------------------------------------

    async def _extract_stage(self, data: dict, pid: uuid.UUID) -> tuple[dict[str, list[Fact]], dict]:
        sem = asyncio.Semaphore(self._concurrency)
        char_names = [c["name"] for c in data["characters"]]
        chapter_title = {ch["id"]: ch.get("title", "") for ch in data["chapters"]}

        # Pre-compute denominators for progress reporting.
        profile_targets = [
            c for c in data["characters"]
            if " ".join(filter(None, [c.get("personality", ""), c.get("appearance", ""), c.get("background", ""), c.get("motivation", "")])).strip()
        ]
        need_extract = [
            s for s in data["scenes"]
            if len(data["contents"].get(s["id"], "")) > self._skip_small
        ]
        total_blocks = sum(
            len(chunk_text(data["contents"][s["id"]], self._block_chars)) for s in need_extract
        )
        total = total_blocks + len(profile_targets) + (1 if data["global_settings"].strip() else 0)
        await self._progress("extract", 0, total, "事实提取开始")

        cache_map = await load_scene_fact_cache(
            self.db, pid,
            [s["id"] for s in data["scenes"] if data["contents"].get(s["id"], "")],
        )

        scene_facts: dict[str, list[Fact]] = {}
        cache_entries: list[dict] = []
        cached_count = 0
        extracted_count = 0
        done = 0

        for scene in data["scenes"]:
            scene_id = scene["id"]
            content = data["contents"].get(scene_id, "")
            facts: list[Fact] = []
            if content:
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                cached = cache_map.get(scene_id)
                if cached and cached["content_hash"] == content_hash:
                    facts = [Fact(**f) for f in cached["facts"]]
                    cached_count += 1
                else:
                    facts = await self._extract_scene(
                        scene, content, chapter_title.get(scene.get("chapter_id"), ""), char_names, sem
                    )
                    extracted_count += 1
                    cache_entries.append({
                        "scene_id": uuid.UUID(scene_id),
                        "content_hash": content_hash,
                        "facts": [f.model_dump() for f in facts],
                        "error": None,
                    })
                    done += len(chunk_text(content, self._block_chars)) if len(content) > self._skip_small else 1
                    await self._progress("extract", done, total, "事实提取中")
            else:
                facts = scene_meta_facts(scene)
            scene_facts[scene_id] = facts

        # Authority baselines: character profiles + world settings.
        baseline_tasks: list[Awaitable[tuple[str, list[Fact]]]] = []
        for c in profile_targets:
            baseline_tasks.append(self._extract_profile(c, char_names, sem))
        if data["global_settings"].strip():
            baseline_tasks.append(self._extract_settings(data["global_settings"], char_names, sem))

        if baseline_tasks:
            baseline_results = await asyncio.gather(*baseline_tasks, return_exceptions=True)
            for r in baseline_results:
                if isinstance(r, tuple):
                    key, facts = r
                    scene_facts[key] = facts
                else:
                    logger.warning("baseline extraction failed", exc_info=r if isinstance(r, BaseException) else None)

        if cache_entries:
            await save_scene_fact_cache(self.db, pid, cache_entries)
            await self.db.flush()

        await self._progress("extract", total, total, "事实提取完成")
        return scene_facts, {"cached": cached_count, "extracted": extracted_count, "total": len(data["scenes"])}

    async def _extract_scene(
        self,
        scene: dict,
        content: str,
        chapter_title: str,
        char_names: list[str],
        sem: asyncio.Semaphore,
    ) -> list[Fact]:
        if len(content) <= self._skip_small:
            return scene_meta_facts(scene)
        blocks = chunk_text(content, self._block_chars)
        results = await asyncio.gather(
            *[
                self._extract_block(scene, chapter_title, block, bi, char_names, sem)
                for bi, block in enumerate(blocks)
            ],
            return_exceptions=True,
        )
        facts = [f for r in results if isinstance(r, list) for f in r]
        facts.extend(scene_meta_facts(scene))
        return facts

    async def _extract_block(
        self,
        scene: dict,
        chapter_title: str,
        block: str,
        block_index: int,
        char_names: list[str],
        sem: asyncio.Semaphore,
    ) -> list[Fact]:
        return await self._extract_with_retry(
            prompts.build_extractor_prompt(chapter_title, scene, block, character_names=char_names),
            prompts.EXTRACTOR_SYSTEM_PROMPT,
            scene_id=scene["id"],
            chapter_id=scene.get("chapter_id"),
            block_index=block_index,
            source_type=SourceType.SCENE_CONTENT,
            sem=sem,
        )

    async def _extract_profile(
        self,
        character: dict,
        char_names: list[str],
        sem: asyncio.Semaphore,
    ) -> tuple[str, list[Fact]]:
        profile_text = " ".join(
            filter(None, [character.get("personality", ""), character.get("appearance", ""), character.get("background", ""), character.get("motivation", "")])
        ).strip()
        scene = {"id": character["id"], "title": character.get("name", ""), "pov_character": "", "setting": "", "scene_time": "", "summary": ""}
        facts = await self._extract_with_retry(
            prompts.build_extractor_prompt("角色设定", scene, profile_text, character_names=char_names),
            prompts.EXTRACTOR_SYSTEM_PROMPT,
            scene_id=character["id"],
            chapter_id=None,
            block_index=0,
            source_type=SourceType.CHARACTER_PROFILE,
            sem=sem,
        )
        return f"profile:{character['id']}", facts

    async def _extract_settings(
        self,
        global_settings: str,
        char_names: list[str],
        sem: asyncio.Semaphore,
    ) -> tuple[str, list[Fact]]:
        scene = {"id": "", "title": "世界观", "pov_character": "", "setting": "", "scene_time": "", "summary": ""}
        facts = await self._extract_with_retry(
            prompts.build_extractor_prompt("世界观设定", scene, global_settings, character_names=char_names),
            prompts.EXTRACTOR_SYSTEM_PROMPT,
            scene_id=None,
            chapter_id=None,
            block_index=0,
            source_type=SourceType.WORLD_SETTINGS,
            sem=sem,
        )
        return "settings", facts

    async def _extract_with_retry(
        self,
        user_prompt: str,
        system_prompt: str,
        *,
        scene_id: str | None,
        chapter_id: str | None,
        block_index: int,
        source_type: SourceType,
        sem: asyncio.Semaphore,
    ) -> list[Fact]:
        async with sem:
            payload = await self._llm_json(
                system_prompt,
                user_prompt,
                max_tokens=settings.consistency_extract_max_tokens,
                reasoning_effort="low",
                temperature=0.0,
            )
            facts = facts_from_extraction(payload or {}, scene_id, chapter_id, block_index, source_type)
            if facts or payload is not None:
                return facts
            # One retry with error feedback.
            retry_user = user_prompt + "\n\n注意：上一次输出不是合法JSON。请只输出一个JSON对象，不要包含任何其他文字。"
            payload = await self._llm_json(
                system_prompt,
                retry_user,
                max_tokens=settings.consistency_extract_max_tokens,
                reasoning_effort="low",
                temperature=0.0,
            )
            return facts_from_extraction(payload or {}, scene_id, chapter_id, block_index, source_type)

    # ------------------------------------------------------------------
    # Stage 2 — merge / alias resolution (Reduce)
    # ------------------------------------------------------------------

    async def _merge_stage(self, data: dict, scene_facts: dict[str, list[Fact]]) -> list[Fact]:
        await self._progress("merge", 0, 1, "归并与别名消解")
        char_id_by_name = {c["name"]: c["id"] for c in data["characters"]}
        all_facts: list[Fact] = []
        for scene_id, facts in scene_facts.items():
            all_facts.extend(facts)

        all_facts = dedup_facts(all_facts)

        # Alias resolution — bounded, one call over the distinct entity set.
        entities = sorted({f.entity for f in all_facts if f.entity})
        if entities and len(all_facts) >= 30 and len(set(char_id_by_name)) > 0:
            aliases = await self._resolve_aliases(entities, list(char_id_by_name.keys()))
            if aliases:
                alias_map = {a: c for c, al in aliases.items() for a in al}
                for f in all_facts:
                    if f.entity in alias_map:
                        f.entity = alias_map[f.entity]
                all_facts = dedup_facts(all_facts)

        await self._progress("merge", 1, 1, "归并完成")
        return all_facts

    async def _resolve_aliases(self, entities: list[str], canonical_names: list[str]) -> dict[str, list[str]]:
        """One small LLM call mapping alias → canonical entity name."""
        if not entities:
            return {}
        system = (
            "你是一个实体别名消解器。给定项目正式角色名单与正文中出现的实体名列表，"
            "判断哪些实体名是同一角色/同一对象的别名。\n"
            "规则：只合并明确指向同一实体的名字（全名/昵称/称呼变体）；不同角色即使名字相近也不合并。\n"
            "输出为JSON：{\"aliases\":[{\"canonical\":\"正式名\",\"alias\":[\"别名1\",\"别名2\"]}]}。\n"
            "canonical 必须是正式名单中的名字。没有别名时输出空列表。"
        )
        user = (
            f"正式名单：{', '.join(canonical_names) if canonical_names else '(空)'}\n"
            f"出现的实体名：{', '.join(entities)}\n"
        )
        payload = await self._llm_json(
            system,
            user,
            max_tokens=settings.consistency_merge_max_tokens,
            reasoning_effort="low",
            temperature=0.0,
        )
        if not payload:
            return {}
        out: dict[str, list[str]] = {}
        for item in payload.get("aliases") or []:
            canonical = str(item.get("canonical", "")).strip()
            aliases = [str(a).strip() for a in (item.get("alias") or []) if str(a).strip()]
            if canonical and canonical in canonical_names and aliases:
                out[canonical] = aliases
        return out

    # ------------------------------------------------------------------
    # Stage 3 — conflict verification
    # ------------------------------------------------------------------

    async def _verify_stage(self, data: dict, merged_facts: list[Fact]) -> list[ConsistencyIssue]:
        candidates = find_conflicts(merged_facts)
        if not candidates:
            return []
        await self._progress("verify", 0, len(candidates), "冲突判定开始")

        char_id_by_name = {c["name"]: c["id"] for c in data["characters"]}
        profiles = [
            {"name": c["name"], "personality": c.get("personality", ""), "appearance": c.get("appearance", ""), "background": c.get("background", "")}
            for c in data["characters"]
        ]
        issues: list[ConsistencyIssue] = []
        batch_size = settings.consistency_verify_batch
        done = 0
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start : start + batch_size]
            verdicts = await self._verify_batch(batch, profiles, data["global_settings"])
            for cand, v in zip(batch, verdicts):
                issue = self._candidate_to_issue(cand, v, char_id_by_name)
                if issue is not None:
                    issues.append(issue)
            done += len(batch)
            await self._progress("verify", done, len(candidates), "冲突判定中")
        await self._progress("verify", len(candidates), len(candidates), "冲突判定完成")
        return issues

    async def _verify_batch(
        self,
        candidates: list[ConflictCandidate],
        profiles: list[dict],
        world_settings: str,
    ) -> list[Verdict]:
        cand_dicts = [c.model_dump() for c in candidates]
        payload = await self._llm_json(
            prompts.VERIFY_SYSTEM_PROMPT,
            prompts.build_verify_prompt(cand_dicts, profiles, world_settings),
            max_tokens=settings.consistency_verify_max_tokens,
            reasoning_effort="medium",
            temperature=0.2,
        )
        if payload is None:
            # Conservative default: every candidate needs human review.
            return [Verdict.NEEDS_REVIEW] * len(candidates)
        raw = payload.get("verdicts") or []
        result: list[Verdict] = []
        for idx in range(len(candidates)):
            entry = next((v for v in raw if isinstance(v, dict) and v.get("candidate_index") == idx), None)
            verdict_str = entry.get("verdict", "") if entry else ""
            try:
                result.append(Verdict(verdict_str))
            except ValueError:
                result.append(Verdict.NEEDS_REVIEW)
        return result

    def _candidate_to_issue(
        self,
        cand: ConflictCandidate,
        verdict: Verdict,
        char_id_by_name: dict[str, str],
    ) -> ConsistencyIssue | None:
        if verdict == Verdict.REAL_INCONSISTENCY:
            severity = "error" if cand.attribute in WORLD_ATTRS or cand.attribute in {"瞳色", "发色", "身份", "能力"} else "warning"
            check_type = "character" if cand.entity in char_id_by_name else "world_rule"
            entity_type = "character" if cand.entity in char_id_by_name else ("timeline" if cand.attribute in TIMELINE_ATTRS else "world")
            entity_id = char_id_by_name.get(cand.entity)
            scene_id = next((v.scene_id for v in cand.values if v.scene_id), None)
            chapter_id = next((v.chapter_id for v in cand.values if v.chapter_id), None)
            values = " vs ".join(v.value for v in cand.values)
            evidences = [v.evidence for v in cand.values if v.evidence]
            return ConsistencyIssue(
                check_type=check_type,
                severity=severity,
                entity_type=entity_type,
                entity_id=entity_id,
                description=f"实体「{cand.entity}」的「{cand.attribute}」前后不一致：{values}",
                suggestion=f"请统一「{cand.entity}」的「{cand.attribute}」，并检查相关场景的叙述",
                chapter_id=chapter_id,
                scene_id=scene_id,
                verdict=verdict,
                evidence=evidences,
            )
        if verdict == Verdict.NEEDS_REVIEW:
            scene_id = next((v.scene_id for v in cand.values if v.scene_id), None)
            chapter_id = next((v.chapter_id for v in cand.values if v.chapter_id), None)
            return ConsistencyIssue(
                check_type="character" if cand.entity in char_id_by_name else "world_rule",
                severity="info",
                entity_type="character" if cand.entity in char_id_by_name else "world",
                entity_id=char_id_by_name.get(cand.entity),
                description=f"「{cand.entity}」的「{cand.attribute}」出现不同描述（{' vs '.join(v.value for v in cand.values)}），建议人工复核",
                suggestion="请人工确认是否有意为之（成长/伪装/闪回/修辞等）",
                chapter_id=chapter_id,
                scene_id=scene_id,
                verdict=verdict,
                evidence=[v.evidence for v in cand.values if v.evidence],
            )
        return None

    # ------------------------------------------------------------------
    # Stage 4 — global review
    # ------------------------------------------------------------------

    async def _global_stage(self, data: dict, scene_facts: dict[str, list[Fact]]) -> list[ConsistencyIssue]:
        scenes_with_facts = []
        for scene in data["scenes"]:
            sid = scene["id"]
            facts = scene_facts.get(sid, [])
            if facts:
                scenes_with_facts.append({**scene, "facts": [f.model_dump() for f in facts]})
        if not scenes_with_facts and not data["chapters"]:
            return []

        sections = [
            prompts.format_chapter_timeline(ch, [s for s in scenes_with_facts if s.get("chapter_id") == ch["id"]])
            for ch in data["chapters"]
        ]
        # Scenes without a resolved chapter (defensive).
        orphan_scenes = [s for s in scenes_with_facts if s.get("chapter_id") not in {ch["id"] for ch in data["chapters"]}]
        for s in orphan_scenes:
            sections.append(prompts.format_chapter_timeline({"id": s.get("chapter_id"), "title": "(未归类场景)"}, [s]))

        await self._progress("global", 0, 1, "全局审查开始")
        prompt = prompts.build_global_prompt(sections, data["chapters"], data["edges"], data["global_settings"])
        budget = int(settings.llm_context_window * settings.consistency_global_budget_ratio)
        if _estimate_tokens(prompt) <= budget:
            issues = await self._global_once(prompt)
        else:
            issues = await self._global_split(sections, data)
        await self._progress("global", 1, 1, "全局审查完成")
        return issues

    async def _global_once(self, prompt: str) -> list[ConsistencyIssue]:
        payload = await self._llm_json(
            prompts.GLOBAL_SYSTEM_PROMPT,
            prompt,
            max_tokens=settings.consistency_global_max_tokens,
            reasoning_effort="medium",
            temperature=0.4,
            timeout=120.0,
        )
        return self._parse_global_issues(payload)

    async def _global_split(self, sections: list[str], data: dict) -> list[ConsistencyIssue]:
        """Budget exceeded: split the timeline in half, review each, then
        merge-review (the design's fold-in-half last resort, §5.4)."""
        mid = max(1, len(sections) // 2)
        issues: list[ConsistencyIssue] = []
        for half in (sections[:mid], sections[mid:]):
            if half:
                sub_prompt = prompts.build_global_prompt(half, data["chapters"], data["edges"], data["global_settings"])
                issues.extend(await self._global_once(sub_prompt))
        if not issues:
            return issues
        # De-duplicate / correlate across halves.
        return self._dedupe_global_issues(issues)

    def _parse_global_issues(self, payload: dict | None) -> list[ConsistencyIssue]:
        if not payload:
            return []
        issues: list[ConsistencyIssue] = []
        for item in payload.get("issues") or []:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity", "info"))
            if severity not in {"error", "warning", "info"}:
                severity = "info"
            try:
                issues.append(
                    ConsistencyIssue(
                        check_type="global",
                        severity=severity,
                        entity_type=str(item.get("entity_type", "chapter")),
                        entity_id=item.get("entity_id"),
                        description=str(item.get("description", "")),
                        suggestion=item.get("suggestion"),
                        chapter_id=item.get("chapter_id"),
                        scene_id=item.get("scene_id"),
                    )
                )
            except Exception:
                logger.warning("global issue parse failed", exc_info=True)
        return issues

    @staticmethod
    def _dedupe_global_issues(issues: list[ConsistencyIssue]) -> list[ConsistencyIssue]:
        seen: set[tuple] = set()
        out: list[ConsistencyIssue] = []
        for i in issues:
            key = (i.check_type, i.severity, i.entity_type, (i.description or "")[:60])
            if key in seen:
                continue
            seen.add(key)
            out.append(i)
        return out
