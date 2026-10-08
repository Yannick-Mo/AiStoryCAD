"""结构写入工具（增 / 改 / 移 / 删）——规格第 9 节。

顺序规范（规格第 5 节）在这里落地：**位置只有一份真相**（层内序号 + 父子归属），
**移动只用"插到谁之后"表达**（工具层永不暴露裸序号——让模型算序号必错），
**序号由实现归一化成连续整数**（这是实现的责任，不是模型的责任，也不是用户的责任）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.blueprint import chapter_hint
from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolMeta, ToolResult, verify_project_owner
from app.storycad.models import Act, Chapter, Setup
from app.storycad.repository import with_chapter_content
from app.storycad.sequence import move_item, ordered_chapter_ids, renumber_container


async def _next_sort_order(db: AsyncSession, model, project_id: uuid.UUID) -> int:
    """容器内最大值 + 1。真正的定位紧接着由 move_item 归一化。"""
    from sqlalchemy import func

    query = select(func.coalesce(func.max(model.sort_order), 0)).where(model.project_id == project_id)
    return int((await db.execute(query)).scalar() or 0) + 1


def _position_arg() -> dict:
    return {
        "type": "string",
        "description": "不传 after_id 时生效：end=接在末尾（默认），start=插到最前",
        "enum": ["start", "end"],
    }


class CreateActTool(BaseTool):
    meta = ToolMeta(
        name="create_act",
        description="新建一幕：幕名 + 「这一幕要干什么」。默认接在最后一幕之后，用 after_id 插到某一幕之后",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "幕名，例如「第一幕」"},
                "purpose": {"type": "string", "description": "这一幕要干什么：这一幕承担什么转变——用一段话说清这一幕整体要完成什么"},
                "after_id": {"type": "string", "description": "插到哪一幕之后（幕ID，来自 read_acts）。不传=接在末尾"},
                "position": _position_arg(),
                "color": {"type": "string", "description": "颜色代码（仅供前端显示）"},
            },
            "required": ["name", "purpose"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            name = self._require_param(kwargs, "name")
            if name is None:
                return self._missing_param("name")

            act = Act(
                project_id=pid,
                name=name,
                purpose=kwargs.get("purpose", "") or "",
                sort_order=await _next_sort_order(db, Act, pid),
            )
            if kwargs.get("color"):
                act.color = kwargs["color"]
            db.add(act)
            await db.flush()

            after_raw = self._require_param(kwargs, "after_id")
            await move_item(
                db, Act, pid,
                item_id=act.id,
                after_id=uuid.UUID(after_raw) if after_raw else None,
                position=kwargs.get("position") or "end",
            )
            await db.commit()
            return ToolResult(success=True, data={
                "act_id": str(act.id),
                "name": act.name,
                "purpose": act.purpose or "",
                "sort_order": act.sort_order,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class UpdateActTool(BaseTool):
    meta = ToolMeta(
        name="update_act",
        description="改幕名 / 改「这一幕要干什么」。位置不在这里改——调整幕序用 move_act",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "act_id": {"type": "string", "description": "幕ID，来自 read_acts"},
                "name": {"type": "string", "description": "幕名"},
                "purpose": {"type": "string", "description": "这一幕要干什么：这一幕承担什么转变"},
                "color": {"type": "string", "description": "颜色代码（仅供前端显示）"},
            },
            "required": ["act_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            act_raw = self._require_param(kwargs, "act_id")
            if act_raw is None:
                return self._missing_param("act_id")
            act = (await db.execute(
                select(Act).where(Act.id == uuid.UUID(act_raw))
            )).scalar_one_or_none()
            if not act:
                return self._not_found("Act")
            await verify_project_owner(db, act.project_id, kwargs.get("user_id"))
            for field in ("name", "purpose", "color"):
                if field in kwargs and kwargs[field] is not None:
                    setattr(act, field, kwargs[field])
            await db.commit()
            return ToolResult(success=True, data={
                "act_id": str(act.id),
                "name": act.name or "",
                "purpose": act.purpose or "",
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class MoveActTool(BaseTool):
    meta = ToolMeta(
        name="move_act",
        description="调整幕序：把某一幕插到另一幕之后（after_id），或插到最前 / 最后（position）"
                    "。不要传序号——你只描述插到哪里",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "act_id": {"type": "string", "description": "要移动的幕ID，来自 read_acts"},
                "after_id": {"type": "string", "description": "插到这一幕之后（幕ID）。不传=见 position"},
                "position": _position_arg(),
            },
            "required": ["act_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            act_raw = self._require_param(kwargs, "act_id")
            if act_raw is None:
                return self._missing_param("act_id")
            act = (await db.execute(
                select(Act).where(Act.id == uuid.UUID(act_raw))
            )).scalar_one_or_none()
            if not act:
                return self._not_found("Act")
            await verify_project_owner(db, act.project_id, kwargs.get("user_id"))

            after_raw = self._require_param(kwargs, "after_id")
            try:
                await move_item(
                    db, Act, act.project_id,
                    item_id=act.id,
                    after_id=uuid.UUID(after_raw) if after_raw else None,
                    position=kwargs.get("position") or "end",
                )
            except LookupError as exc:
                return ToolResult(success=False, error=str(exc),
                                  correction_hint="下一步：先用 read_acts 取最新的幕ID，再指定 after_id")
            await db.commit()
            return ToolResult(success=True, data={"act_id": str(act.id), "sort_order": act.sort_order})
        except Exception as e:
            await db.rollback()
            return self._err(e)


class DeleteActTool(BaseTool):
    meta = ToolMeta(
        name="delete_act",
        description="删除一幕——**连带它里面的所有章和正文**。只是想给章换个幕请用 move_chapter",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "act_id": {"type": "string", "description": "要删除的幕ID，来自 read_acts"},
            },
            "required": ["act_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            act_raw = self._require_param(kwargs, "act_id")
            if act_raw is None:
                return self._missing_param("act_id")
            act = (await db.execute(
                select(Act).where(Act.id == uuid.UUID(act_raw))
            )).scalar_one_or_none()
            if not act:
                return self._not_found("Act")
            await verify_project_owner(db, act.project_id, kwargs.get("user_id"))
            project_id, act_id = act.project_id, act.id

            # 这一幕里有多少章／多少正文字符——删之前先如实报出来
            from sqlalchemy import func
            chapter_rows = (await db.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(func.length(func.coalesce(Chapter.content, ""))), 0),
                ).where(Chapter.act_id == act_id)
            )).one()
            # 连带删掉的章 id 要如实交出去：ID 寄存表靠它把这些条目标为失效。
            deleted_chapter_ids = [
                str(cid) for (cid,) in (await self.db.execute(
                    select(Chapter.id).where(Chapter.act_id == act_id)
                )).all()
            ]
            # 埋在这些章里的伏笔会随章一起被级联删掉，同样要交出去。
            _chapter_uuids = [uuid.UUID(c) for c in deleted_chapter_ids]
            deleted_setup_ids = [
                str(sid) for (sid,) in (await self.db.execute(
                    select(Setup.id).where(Setup.planted_in.in_(_chapter_uuids))
                )).all()
            ] if _chapter_uuids else []

            await db.delete(act)
            await db.flush()
            await renumber_container(db, Act, project_id)
            await db.commit()
            return ToolResult(success=True, data={
                "deleted_act_id": str(act_id),
                "deleted_chapters": int(chapter_rows[0] or 0),
                "deleted_chapter_ids": deleted_chapter_ids,
                "deleted_setup_ids": deleted_setup_ids,
                "deleted_body_chars": int(chapter_rows[1] or 0),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class CreateChapterTool(BaseTool):
    meta = ToolMeta(
        name="create_chapter",
        description="新建章，**一次可传多章**（建 30 章不必调 30 次）。每章给「本章要写什么」。"
                    "默认接在所属幕末尾，用 after_id 插到某一章之后。正文用 write_chapter 写",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "act_id": {"type": "string", "description": "所属幕ID，来自 read_acts。章必须有幕——没有幕就没有位置"},
                "chapters": {
                    "type": "array",
                    "description": "要新建的章，按给定顺序依次插入",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "章名"},
                            "goal": {"type": "string", "description": chapter_hint()},
                        },
                        "required": ["title"],
                    },
                },
                "after_id": {"type": "string", "description": "把这些章插到这一章之后（章ID）。不传=接在所属幕末尾"},
            },
            "required": ["act_id", "chapters"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            act_raw = self._require_param(kwargs, "act_id")
            if act_raw is None:
                return self._missing_param("act_id")
            act_id = uuid.UUID(act_raw)
            act = (await db.execute(select(Act).where(Act.id == act_id))).scalar_one_or_none()
            if act is None or act.project_id != pid:
                return self._not_found("Act in project")

            payload = kwargs.get("chapters")
            if not payload or not isinstance(payload, list):
                return ToolResult(
                    success=False,
                    error="chapters 必须是一个非空数组，每项含 title（章名），可选 goal（本章要写什么）",
                )

            created: list[Chapter] = []
            for index, item in enumerate(payload):
                if not isinstance(item, dict) or not (item.get("title") or "").strip():
                    return ToolResult(success=False, error=f"chapters 第 {index + 1} 项缺少 title")
                chapter = Chapter(
                    project_id=pid,
                    act_id=act_id,
                    title=item["title"],
                    goal=item.get("goal", "") or "",
                    status="draft",
                    sort_order=await _next_sort_order(db, Chapter, pid) + index,
                )
                db.add(chapter)
                created.append(chapter)
            await db.flush()

            # 整批移到目标位置：先放第一批，再依次接在它后面。
            anchor_raw = self._require_param(kwargs, "after_id")
            anchor = uuid.UUID(anchor_raw) if anchor_raw else None
            for chapter in created:
                await move_item(db, Chapter, pid, item_id=chapter.id, after_id=anchor,
                                position="end", container_column="act_id", container_id=act_id)
                anchor = chapter.id

            await db.commit()
            return ToolResult(success=True, data={
                "act_id": str(act_id),
                "created_count": len(created),
                "chapters": [
                    {"id": str(c.id), "title": c.title, "goal": c.goal or "",
                     "sort_order": c.sort_order, "status": c.status}
                    for c in created
                ],
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class UpdateChapterTool(BaseTool):
    meta = ToolMeta(
        name="update_chapter",
        description="改章：章名 / 本章要写什么 / 状态（draft · revising · final）。"
                    "位置不在这里改：换幕或重排用 move_chapter。"
                    "**正文不算这一章的前情**——下一章直接读上一章结尾原文，"
                    "所以不需要（也没有）\"本章写成了什么\"这个字段",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 read_chapters 或 read_acts"},
                "title": {"type": "string", "description": "章名"},
                "goal": {"type": "string", "description": chapter_hint()},
                "status": {
                    "type": "string",
                    "description": "这一章算不算过：draft（草稿）/ revising（修订中）/ final（终稿）",
                    "enum": ["draft", "revising", "final"],
                },
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            chapter = (await db.execute(
                select(Chapter).where(Chapter.id == uuid.UUID(ch_raw))
            )).scalar_one_or_none()
            if not chapter:
                return self._not_found("Chapter")
            await verify_project_owner(db, chapter.project_id, kwargs.get("user_id"))

            if "status" in kwargs and kwargs["status"] is not None:
                status = kwargs["status"]
                if status not in ("draft", "revising", "final"):
                    return ToolResult(
                        success=False,
                        error=f"无效状态 '{status}'，有效值为：draft, revising, final",
                        correction_hint="请把 status 设为 draft（草稿）、revising（修订中）或 final（终稿）之一",
                    )
                chapter.status = status
            for field in ("title", "goal"):
                if field in kwargs and kwargs[field] is not None:
                    setattr(chapter, field, kwargs[field])
            await db.commit()
            return ToolResult(success=True, data={
                "chapter_id": str(chapter.id),
                "title": chapter.title or "",
                "status": chapter.status or "",
                "has_goal": bool((chapter.goal or "").strip()),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class MoveChapterTool(BaseTool):
    meta = ToolMeta(
        name="move_chapter",
        description="移动章：**换幕**（act_id）和／或幕内重排（after_id 插到某章之后，"
                    "或 position=start/end）。不要传序号——你只描述插到哪里；序号由服务端归一化成连续整数",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "要移动的章ID，来自 read_chapters"},
                "act_id": {"type": "string", "description": "移到哪一幕（幕ID，来自 read_acts）。不传=留在原幕，只重排"},
                "after_id": {"type": "string", "description": "插到这一章之后（章ID，必须在目标幕里）。不传=见 position"},
                "position": _position_arg(),
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            chapter = (await db.execute(
                select(Chapter).where(Chapter.id == uuid.UUID(ch_raw))
            )).scalar_one_or_none()
            if not chapter:
                return self._not_found("Chapter")
            await verify_project_owner(db, chapter.project_id, kwargs.get("user_id"))
            project_id = chapter.project_id

            act_raw = self._require_param(kwargs, "act_id")
            target_act_id = uuid.UUID(act_raw) if act_raw else chapter.act_id
            if act_raw:
                act = (await db.execute(select(Act).where(Act.id == target_act_id))).scalar_one_or_none()
                if act is None or act.project_id != project_id:
                    return self._not_found("Act in project")

            after_raw = self._require_param(kwargs, "after_id")
            try:
                await move_item(
                    db, Chapter, project_id,
                    item_id=chapter.id,
                    after_id=uuid.UUID(after_raw) if after_raw else None,
                    position=kwargs.get("position") or "end",
                    container_column="act_id",
                    container_id=target_act_id,
                )
            except LookupError as exc:
                return ToolResult(
                    success=False, error=str(exc),
                    correction_hint="下一步：用 read_chapters 确认目标幕里的章ID，再指定 after_id（after_id 必须在目标幕内）",
                )
            await db.commit()

            ordered = await ordered_chapter_ids(db, project_id)
            return ToolResult(success=True, data={
                "chapter_id": str(chapter.id),
                "act_id": str(target_act_id),
                "sort_order": chapter.sort_order,
                "global_order": (ordered.index(chapter.id) + 1) if chapter.id in ordered else None,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class DeleteChapterTool(BaseTool):
    meta = ToolMeta(
        name="delete_chapter",
        description="删除一章——**连带它的正文**，以及埋在这一章里的伏笔记录（打算在这一章收的伏笔只是失去计划，"
                    "伏笔本身还在）",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "要删除的章ID，来自 read_chapters"},
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            chapter = (await db.execute(
                with_chapter_content(select(Chapter).where(Chapter.id == uuid.UUID(ch_raw)))
            )).scalar_one_or_none()
            if not chapter:
                return self._not_found("Chapter")
            await verify_project_owner(db, chapter.project_id, kwargs.get("user_id"))
            project_id, act_id, chapter_id = chapter.project_id, chapter.act_id, chapter.id
            body_chars = len(chapter.content or "")
            # 埋在这一章里的伏笔会被级联删掉（打算在这一章收的只是失去计划，不删）。
            deleted_setup_ids = [
                str(sid) for (sid,) in (await db.execute(
                    select(Setup.id).where(Setup.planted_in == chapter_id)
                )).all()
            ]

            await db.delete(chapter)
            await db.flush()
            await renumber_container(db, Chapter, project_id, "act_id", act_id)
            await db.commit()
            return ToolResult(success=True, data={
                "deleted_chapter_id": str(chapter_id),
                "deleted_setup_ids": deleted_setup_ids,
                "deleted_body_chars": body_chars,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)
