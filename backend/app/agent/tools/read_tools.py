"""读取工具（#2，12 个）——docs/故事模型设计规格.md 第 8 节。

五种访问形状：读一个节点 / 读子节点清单 / 读一个顺序范围 / 读一个决策用的聚合包 /
按关键词找。**工具数量由访问形状决定，不由对象数量决定。**

五个"故意不做"（规格第 8 节）：没有"读单个伏笔"（一条伏笔就是一句话 + 两个引用 +
一个状态，清单一次读完，不为对称加工具）· 没有"读单个幕"（幕只有 3-5 个）·
没有"按幕读章清单"（read_acts 已给出每幕章数与起止全局章号）· 没有独立的"重写"工具
（write_chapter 本来就是整体替换）· 四个分析类工具全部消失。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ContextBuilder
from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolMeta, ToolResult, verify_project_owner
from app.project.models import Project
from app.storycad.models import Act, Chapter, Character, CharacterRelation, Setup
from app.storycad.order import order_by_sequence
from app.storycad.repository import with_chapter_content
from app.storycad.sequence import ordered_chapter_ids


async def _chapter_index(db: AsyncSession, project_id: uuid.UUID) -> dict[uuid.UUID, dict[str, Any]]:
    """章 id → 全局章号 + 章名。全局章号是算出来的，不存。"""
    ordered = await ordered_chapter_ids(db, project_id)
    titles = dict((await db.execute(
        select(Chapter.id, Chapter.title).where(Chapter.project_id == project_id)
    )).all())
    return {
        cid: {"global_order": index + 1, "title": (titles.get(cid) or "")}
        for index, cid in enumerate(ordered)
    }


def _snippet(text: str, keyword: str, radius: int = 90) -> str:
    """命中位置附近的片段——让模型知道命中的是计划还是正文。"""
    if not text:
        return ""
    idx = text.lower().find(keyword.lower())
    if idx < 0:
        return text[: radius * 2]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(keyword) + radius)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


class ReadProjectTool(BaseTool):
    meta = ToolMeta(
        name="read_project",
        description="读作品层：书名 / 类型 / 一句话梗概 / 终局 / 主线骨架 / 全局设定全文 / 目标字数。"
                    "终局、主线骨架、全局设定一次读完（全局设定给全文，不是常驻里的前 2000 字）"
                    "。正文不在这里，用 read_chapter_content",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        max_result_chars=100000,
        parameters={"type": "object", "properties": {}},
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            project = (await db.execute(
                select(Project).where(Project.id == pid)
            )).scalar_one_or_none()
            if not project:
                return self._not_found("Project")
            settings = project.global_settings or ""
            return ToolResult(success=True, data={
                "id": str(project.id),
                "title": project.title or "",
                "genre": project.genre or "",
                "logline": project.logline or "",
                "ending": project.ending or "",
                "ending_chars": len(project.ending or ""),
                "spine": project.spine or "",
                "global_settings": settings,
                "global_settings_chars": len(settings),
                "total_words": int(project.total_words or 0),
                "status": project.status or "",
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadActsTool(BaseTool):
    meta = ToolMeta(
        name="read_acts",
        description="读全部幕：每一幕的「这一幕要干什么」、有几章、起止全局章号。要看某一幕里的章清单，"
                    "用 read_chapters（按全局章号范围）",
        concurrency=ConcurrencyMode.SAFE,
        parameters={"type": "object", "properties": {}},
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            acts = (await db.execute(
                select(Act).where(Act.project_id == pid).order_by(*order_by_sequence(Act))
            )).scalars().all()

            index = await _chapter_index(db, pid)
            spans: dict[uuid.UUID, list[int]] = {}
            for chapter_id, act_id in (await db.execute(
                select(Chapter.id, Chapter.act_id).where(Chapter.project_id == pid)
            )).all():
                info = index.get(chapter_id)
                if not info:
                    continue
                span = spans.setdefault(act_id, [info["global_order"], info["global_order"]])
                span[0] = min(span[0], info["global_order"])
                span[1] = max(span[1], info["global_order"])

            data = []
            for act in acts:
                span = spans.get(act.id)
                data.append({
                    "id": str(act.id),
                    "name": act.name or "",
                    "purpose": act.purpose or "",
                    "sort_order": act.sort_order,
                    "color": act.color or "",
                    "chapter_count": (span[1] - span[0] + 1) if span else 0,
                    "first_chapter_order": span[0] if span else None,
                    "last_chapter_order": span[1] if span else None,
                })
            return ToolResult(success=True, data={
                "acts": data,
                "act_count": len(data),
                "chapter_count": len(index),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadChaptersTool(BaseTool):
    meta = ToolMeta(
        name="read_chapters",
        description="读第 X 到第 Y 章：每章的 本章要写什么 + 本章写成了什么 + 状态 + 所属幕 + 是否已有正文（回看前情的主力）"
                    "。全局章号从第 1 幕第 1 章起跨幕连续编号。**粗看很长的范围用 detail=index**："
                    "只给 章号/标题/状态/幕/是否已写，同样预算能读的章数是默认模式的三倍多。正文不在这里（用 read_chapter_content）"
                    "；动一章之前的全部输入用 read_chapter_package。truncated=true 时用 next_from 接着读",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        max_result_chars=22000,
        parameters={
            "type": "object",
            "properties": {
                "chapter_from": {"type": "integer", "description": "全局章序号起点（从1开始，含）"},
                "chapter_to": {"type": "integer", "description": "全局章序号终点（含）"},
                "detail": {"type": "string", "enum": ["full", "index"],
                           "description": "full（默认）=每章的计划与结果，回看前情用它；"
                                          "index=只给 章号/标题/状态/幕/有没有正文，粗看长范围（例如全书目录）用它"},
            },
            "required": ["chapter_from", "chapter_to"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            try:
                chapter_from = int(kwargs.get("chapter_from"))
                chapter_to = int(kwargs.get("chapter_to"))
            except (TypeError, ValueError):
                return ToolResult(
                    success=False,
                    error="chapter_from / chapter_to 必须是整数（全局章序号，从1开始）",
                )
            if chapter_from < 1 or chapter_to < 1:
                return ToolResult(success=False, error="全局章序号从 1 开始（第1幕第1章=1）")
            if chapter_from > chapter_to:
                return ToolResult(
                    success=False,
                    error="章节起点大于终点（chapter_from > chapter_to），请检查序号",
                )

            detail = str(kwargs.get("detail") or "full")
            if detail not in ("full", "index"):
                return ToolResult(
                    success=False,
                    error=f"无效的 detail '{detail}'，只能是 full（默认）或 index",
                    correction_hint="回看前情用 full；只想粗看长范围（例如全书目录）用 index",
                )
            builder = ContextBuilder(db, redis_client=kwargs.get("redis_client"))
            data = await builder.build_chapter_window(
                pid, chapter_from, chapter_to, detail=detail)
            return ToolResult(success=True, data=data)
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadChapterContentTool(BaseTool):
    meta = ToolMeta(
        name="read_chapter_content",
        description="读某一章的正文（唯一的正文读取工具）。只在要改它或接续它的时候读。默认一次返回完整正文；"
                    "超过单次上限或只想看某一段时用 content_offset/content_limit 定位，"
                    "content_has_more=true 表示还有后续",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        max_result_chars=100000,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 read_chapters 或 read_acts"},
                "content_offset": {"type": "integer", "description": "正文读取起点（字符偏移，默认0=从头）"},
                "content_limit": {"type": "integer", "description": "最多读取长度（字符数）；不传或0=读到末尾/单次上限"},
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            ch_id = uuid.UUID(ch_raw)
            chapter = (await db.execute(
                with_chapter_content(select(Chapter).where(Chapter.id == ch_id))
            )).scalar_one_or_none()
            if not chapter:
                return self._not_found("Chapter")
            await verify_project_owner(db, chapter.project_id, kwargs.get("user_id"))

            body = chapter.content or ""
            try:
                offset = int(kwargs.get("content_offset") or 0)
            except (TypeError, ValueError):
                offset = 0
            offset = max(0, offset)
            try:
                limit = int(kwargs.get("content_limit") or 0)
            except (TypeError, ValueError):
                limit = 0
            max_page = self._effective_max_result_chars - 1000
            if limit <= 0:
                limit = max_page
            limit = min(limit, max_page)
            page = body[offset:offset + limit]
            return ToolResult(success=True, data={
                "chapter_id": str(chapter.id),
                "chapter_title": chapter.title or "",
                "body_chars": len(body),
                "content_offset": offset,
                "content": page,
                "content_has_more": (offset + len(page)) < len(body),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadChapterPackageTool(BaseTool):
    meta = ToolMeta(
        name="read_chapter_package",
        description="动一章之前要的全部输入，一次拿齐：这一章的计划与结果、上一章的结果（本章的起点）"
                    "、它在整本书里的位置（这一幕要干什么 + 主线骨架 + 终局）、人物动机、还没收的伏笔。"
                    "本章正文只给长度和尾部片段。写、改、或判断该接写还是重写之前先调这个",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        max_result_chars=30000,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 read_chapters 或 read_acts"},
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            ch_id = uuid.UUID(ch_raw)

            builder = ContextBuilder(db, redis_client=kwargs.get("redis_client"))
            package = await builder.build_chapter_package(pid, ch_id)
            if package is None:
                return self._not_found("Chapter in project")
            return ToolResult(success=True, data=package)
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadCharacterTool(BaseTool):
    meta = ToolMeta(
        name="read_character",
        description="读一个人物的完整档案：姓名 / 定位 / 性格 / 外貌 / 背景 / 他想要什么 / 他从哪到哪，"
                    "以及他与别人之间的关系。character_id 来自 list_characters",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "人物ID，来自 list_characters"},
            },
            "required": ["character_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            cid_raw = self._require_param(kwargs, "character_id")
            if cid_raw is None:
                return self._missing_param("character_id")
            cid = uuid.UUID(cid_raw)
            char = (await db.execute(
                select(Character).where(Character.id == cid)
            )).scalar_one_or_none()
            if not char:
                return self._not_found("Character")
            await verify_project_owner(db, char.project_id, kwargs.get("user_id"))

            rels = (await db.execute(
                select(CharacterRelation).where(
                    CharacterRelation.project_id == char.project_id,
                    or_(CharacterRelation.character_id == cid, CharacterRelation.target_id == cid),
                )
            )).scalars().all()
            names = dict((await db.execute(
                select(Character.id, Character.name).where(Character.project_id == char.project_id)
            )).all())

            return ToolResult(success=True, data={
                "id": str(char.id),
                "name": char.name or "",
                "role": char.role or "",
                "personality": char.personality or "",
                "appearance": char.appearance or "",
                "background": char.background or "",
                "motivation": char.motivation or "",
                "arc": char.arc or "",
                "relations": [
                    {
                        "id": str(rel.id),
                        "with": names.get(
                            rel.target_id if rel.character_id == cid else rel.character_id, "?"
                        ),
                        "rel_type": rel.rel_type or "",
                        "label": rel.label or "",
                        "description": rel.description or "",
                    }
                    for rel in rels
                ],
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadRelationTool(BaseTool):
    meta = ToolMeta(
        name="read_relation",
        description="读一条角色关系的来龙去脉。只要名录用 list_relations 更省。relation_id 来自 list_relations",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "relation_id": {"type": "string", "description": "关系ID，来自 list_relations"},
            },
            "required": ["relation_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            rid_raw = self._require_param(kwargs, "relation_id")
            if rid_raw is None:
                return self._missing_param("relation_id")
            rid = uuid.UUID(rid_raw)
            rel = (await db.execute(
                select(CharacterRelation).where(CharacterRelation.id == rid)
            )).scalar_one_or_none()
            if not rel:
                return self._not_found("CharacterRelation")
            await verify_project_owner(db, rel.project_id, kwargs.get("user_id"))
            names = dict((await db.execute(
                select(Character.id, Character.name).where(Character.project_id == rel.project_id)
            )).all())
            return ToolResult(success=True, data={
                "id": str(rel.id),
                "character_id": str(rel.character_id),
                "character_name": names.get(rel.character_id, ""),
                "target_id": str(rel.target_id),
                "target_name": names.get(rel.target_id, ""),
                "rel_type": rel.rel_type or "",
                "label": rel.label or "",
                "description": rel.description or "",
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ListCharactersTool(BaseTool):
    meta = ToolMeta(
        name="list_characters",
        description="列出有哪几个人（姓名 + 定位 + ID）。某个人的完整档案用 read_character",
        concurrency=ConcurrencyMode.SAFE,
        parameters={"type": "object", "properties": {}},
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            chars = (await db.execute(
                select(Character).where(Character.project_id == pid)
                .order_by(*order_by_sequence(Character))
            )).scalars().all()
            return ToolResult(success=True, data={
                "characters": [
                    {
                        "id": str(c.id),
                        "name": c.name or "",
                        "role": c.role or "",
                        "sort_order": c.sort_order,
                        "has_motivation": bool((c.motivation or "").strip()),
                        "has_arc": bool((c.arc or "").strip()),
                    }
                    for c in chars
                ],
                "count": len(chars),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ListRelationsTool(BaseTool):
    meta = ToolMeta(
        name="list_relations",
        description="列出有哪些角色关系：谁跟谁、什么关系、更具体的说法。要某一条的来龙去脉用 read_relation",
        concurrency=ConcurrencyMode.SAFE,
        parameters={"type": "object", "properties": {}},
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            rels = (await db.execute(
                select(CharacterRelation).where(CharacterRelation.project_id == pid)
            )).scalars().all()
            names = dict((await db.execute(
                select(Character.id, Character.name).where(Character.project_id == pid)
            )).all())
            return ToolResult(success=True, data={
                "relations": [
                    {
                        "id": str(rel.id),
                        "character_id": str(rel.character_id),
                        "character_name": names.get(rel.character_id, ""),
                        "target_id": str(rel.target_id),
                        "target_name": names.get(rel.target_id, ""),
                        "rel_type": rel.rel_type or "",
                        "label": rel.label or "",
                    }
                    for rel in rels
                ],
                "count": len(rels),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ListSetupsTool(BaseTool):
    meta = ToolMeta(
        name="list_setups",
        description="列出伏笔：埋的是什么、埋在哪一章、打算在哪一章收、收没收。可按 status 筛选",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "只看某个状态：open（还没收）/ paid（已收）/ dropped（不打算收了）。不传=全部",
                    "enum": ["open", "paid", "dropped"],
                },
            },
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            status = kwargs.get("status")
            if status not in (None, "", "open", "paid", "dropped"):
                return ToolResult(
                    success=False,
                    error=f"无效的 status：{status}（只能是 open / paid / dropped，或不传）",
                )
            query = select(Setup).where(Setup.project_id == pid)
            if status:
                query = query.where(Setup.status == status)
            setups = (await db.execute(query.order_by(*order_by_sequence(Setup)))).scalars().all()

            index = await _chapter_index(db, pid)

            def _ref(chapter_id):
                if not chapter_id:
                    return {"chapter_id": None, "global_order": None, "title": ""}
                info = index.get(chapter_id, {})
                return {
                    "chapter_id": str(chapter_id),
                    "global_order": info.get("global_order"),
                    "title": info.get("title", ""),
                }

            return ToolResult(success=True, data={
                "setups": [
                    {
                        "id": str(s.id),
                        "note": s.note or "",
                        "status": s.status or "open",
                        "planted_in": _ref(s.planted_in),
                        "pay_off_in": _ref(s.pay_off_in),
                    }
                    for s in setups
                ],
                "count": len(setups),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadProgressTool(BaseTool):
    meta = ToolMeta(
        name="read_progress",
        description="读进度——**算出来的清单，不是表**：设计缺什么 · 正文断点在第几章（下一章该写的就是它）"
                    "· 伏笔逾期 · 定稿进度 · 最近改动的章 · 下一步做什么。不确定接下来干什么时先调这个",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        parameters={"type": "object", "properties": {}},
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            builder = ContextBuilder(db, redis_client=kwargs.get("redis_client"))
            return ToolResult(success=True, data=await builder.build_progress(pid))
        except Exception as e:
            await db.rollback()
            return self._err(e)


class SearchTool(BaseTool):
    meta = ToolMeta(
        name="search",
        description="按关键词在**整个项目**里找（含正文：章正文、计划、结果、幕、人物、关系、伏笔）"
                    "，每条命中标出命中的是哪个字段。适合「那枚旧硬币在哪一章提过」这类定位",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        max_result_chars=20000,
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词（不能为空字符串）"},
                "limit": {"type": "integer", "description": "每类最多返回几条（1-20，默认8）"},
            },
            "required": ["keyword"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            keyword = self._require_param(kwargs, "keyword")
            if keyword is None:
                return self._missing_param("keyword")
            try:
                limit = int(kwargs.get("limit") or 8)
            except (TypeError, ValueError):
                limit = 8
            limit = max(1, min(limit, 20))
            like = f"%{keyword}%"
            lowered = keyword.lower()

            index = await _chapter_index(db, pid)
            hits: list[dict[str, Any]] = []

            def _fields(pairs) -> list[dict[str, str]]:
                return [
                    {"field": name, "snippet": _snippet(value, keyword)}
                    for name, value in pairs
                    if value and lowered in value.lower()
                ]

            # 章：正文与两个蓝图字段一起搜——命中哪个字段必须标出来
            chapter_rows = (await db.execute(
                select(
                    Chapter.id, Chapter.title, Chapter.goal, Chapter.result,
                    Chapter.content, Chapter.status,
                    func.length(func.coalesce(Chapter.content, "")).label("body_chars"),
                )
                .where(Chapter.project_id == pid)
                .where(or_(
                    Chapter.title.ilike(like),
                    Chapter.goal.ilike(like),
                    Chapter.result.ilike(like),
                    Chapter.content.ilike(like),
                ))
                .order_by(*order_by_sequence(Chapter))
                .limit(limit)
            )).all()
            for row in chapter_rows:
                info = index.get(row.id, {})
                hits.append({
                    "kind": "chapter",
                    "id": str(row.id),
                    "global_order": info.get("global_order"),
                    "title": row.title or "",
                    "status": row.status or "",
                    "body_chars": int(row.body_chars or 0),
                    "matches": _fields((
                        ("title", row.title), ("goal", row.goal),
                        ("result", row.result), ("content", row.content),
                    )),
                })

            act_rows = (await db.execute(
                select(Act.id, Act.name, Act.purpose)
                .where(Act.project_id == pid)
                .where(or_(Act.name.ilike(like), Act.purpose.ilike(like)))
                .order_by(*order_by_sequence(Act))
                .limit(limit)
            )).all()
            for row in act_rows:
                hits.append({
                    "kind": "act",
                    "id": str(row.id),
                    "name": row.name or "",
                    "matches": _fields((("name", row.name), ("purpose", row.purpose))),
                })

            char_rows = (await db.execute(
                select(Character.id, Character.name, Character.personality, Character.appearance,
                       Character.background, Character.motivation, Character.arc)
                .where(Character.project_id == pid)
                .where(or_(
                    Character.name.ilike(like), Character.personality.ilike(like),
                    Character.appearance.ilike(like), Character.background.ilike(like),
                    Character.motivation.ilike(like), Character.arc.ilike(like),
                ))
                .order_by(*order_by_sequence(Character))
                .limit(limit)
            )).all()
            for row in char_rows:
                hits.append({
                    "kind": "character",
                    "id": str(row.id),
                    "name": row.name or "",
                    "matches": _fields((
                        ("name", row.name), ("personality", row.personality),
                        ("appearance", row.appearance), ("background", row.background),
                        ("motivation", row.motivation), ("arc", row.arc),
                    )),
                })

            setup_rows = (await db.execute(
                select(Setup.id, Setup.note, Setup.status, Setup.planted_in)
                .where(Setup.project_id == pid, Setup.note.ilike(like))
                .order_by(*order_by_sequence(Setup))
                .limit(limit)
            )).all()
            for row in setup_rows:
                hits.append({
                    "kind": "setup",
                    "id": str(row.id),
                    "note": row.note or "",
                    "status": row.status or "",
                    "planted_in_order": index.get(row.planted_in, {}).get("global_order"),
                    "matches": _fields((("note", row.note),)),
                })

            relation_rows = (await db.execute(
                select(CharacterRelation.id, CharacterRelation.label,
                       CharacterRelation.description, CharacterRelation.rel_type)
                .where(CharacterRelation.project_id == pid)
                .where(or_(
                    CharacterRelation.label.ilike(like),
                    CharacterRelation.description.ilike(like),
                    CharacterRelation.rel_type.ilike(like),
                ))
                .limit(limit)
            )).all()
            for row in relation_rows:
                hits.append({
                    "kind": "relation",
                    "id": str(row.id),
                    "rel_type": row.rel_type or "",
                    "label": row.label or "",
                    "matches": _fields((
                        ("label", row.label), ("description", row.description),
                        ("rel_type", row.rel_type),
                    )),
                })

            hits.sort(key=lambda hit: hit.get("global_order") or 10 ** 6)
            return ToolResult(success=True, data={
                "keyword": keyword,
                "hit_count": len(hits),
                "hits": hits,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)
