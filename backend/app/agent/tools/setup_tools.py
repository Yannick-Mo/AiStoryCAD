"""伏笔（setups）写入工具——规格第 9 节「关系」组。

伏笔是**一对配对引用**（埋在哪一章、打算在哪一章收），不是一条线——这也正是
数据层不需要图的原因之一。

**有 delete_setup**：不打算收了就把 status 改成 dropped——那是**一条信息**（「我放弃收它了」），
它表达不了「这条记录建错了 / 建重了」。所以「放弃收」归 update_setup，「不该存在」归 delete_setup。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolMeta, ToolResult, verify_project_owner
from app.storycad.models import Chapter, Setup


async def _resolve_chapter(db: AsyncSession, project_id: uuid.UUID, raw: str):
    """把工具参数里的章 id 解析成同一项目下的行，顺便校验归属。"""
    chapter_id = uuid.UUID(raw)
    chapter = (await db.execute(select(Chapter).where(Chapter.id == chapter_id))).scalar_one_or_none()
    if chapter is None or chapter.project_id != project_id:
        return None
    return chapter


class CreateSetupTool(BaseTool):
    meta = ToolMeta(
        name="create_setup",
        description="记一条伏笔：埋的是什么（note）、埋在哪一章（planted_in）、打算在哪一章收（pay_off_in，"
                    "**可以不填**）。刚写完一章发现埋了东西时调这个",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "这个伏笔埋的是什么——一句话说清那个物件/那句话/那件事"},
                "planted_in": {"type": "string", "description": "埋在哪一章（章ID，来自 read_chapters）"},
                "pay_off_in": {"type": "string", "description": "打算在哪一章收（章ID）。还没想好就不传"},
            },
            "required": ["note", "planted_in"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            note = self._require_param(kwargs, "note")
            if note is None:
                return self._missing_param("note")
            planted_raw = self._require_param(kwargs, "planted_in")
            if planted_raw is None:
                return self._missing_param("planted_in")
            planted = await _resolve_chapter(db, pid, planted_raw)
            if planted is None:
                return self._not_found("Chapter in project")

            pay_off = None
            pay_off_raw = self._require_param(kwargs, "pay_off_in")
            if pay_off_raw:
                row = await _resolve_chapter(db, pid, pay_off_raw)
                if row is None:
                    return self._not_found("Chapter in project")
                pay_off = row.id

            setup = Setup(project_id=pid, note=note, planted_in=planted.id,
                          pay_off_in=pay_off, status="open")
            db.add(setup)
            await db.commit()
            return ToolResult(success=True, data={
                "setup_id": str(setup.id),
                "note": setup.note,
                "planted_in": str(planted.id),
                "planted_in_title": planted.title or "",
                "pay_off_in": str(pay_off) if pay_off else None,
                "status": setup.status,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class UpdateSetupTool(BaseTool):
    meta = ToolMeta(
        name="update_setup",
        description="改伏笔：改内容（note）、改埋在哪一章、改打算在哪一章收，或改状态——**open 还没收 / paid 已收 / dropped 不打算收了**。"
                    "收了之后把 status 改成 paid 并填上 pay_off_in",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "setup_id": {"type": "string", "description": "伏笔ID，来自 list_setups"},
                "note": {"type": "string", "description": "这个伏笔埋的是什么"},
                "planted_in": {"type": "string", "description": "改埋在哪一章（章ID）"},
                "pay_off_in": {"type": "string", "description": "改打算在哪一章收（章ID）"},
                "status": {
                    "type": "string",
                    "description": "收没收：open（还没收）/ paid（已收）/ dropped（不打算收了）",
                    "enum": ["open", "paid", "dropped"],
                },
            },
            "required": ["setup_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            sid_raw = self._require_param(kwargs, "setup_id")
            if sid_raw is None:
                return self._missing_param("setup_id")
            setup = (await db.execute(
                select(Setup).where(Setup.id == uuid.UUID(sid_raw))
            )).scalar_one_or_none()
            if not setup:
                return self._not_found("Setup")
            await verify_project_owner(db, setup.project_id, kwargs.get("user_id"))

            if "status" in kwargs and kwargs["status"] is not None:
                status = kwargs["status"]
                if status not in ("open", "paid", "dropped"):
                    return ToolResult(
                        success=False,
                        error=f"无效状态 '{status}'，有效值为：open, paid, dropped",
                        correction_hint="请把 status 设为 open（还没收）、paid（已收）或 dropped（不打算收了）",
                    )
                setup.status = status
            if "note" in kwargs and kwargs["note"] is not None:
                setup.note = kwargs["note"]
            for field in ("planted_in", "pay_off_in"):
                raw = self._require_param(kwargs, field)
                if raw:
                    row = await _resolve_chapter(db, setup.project_id, raw)
                    if row is None:
                        return self._not_found("Chapter in project")
                    setattr(setup, field, row.id)
            await db.commit()
            return ToolResult(success=True, data={
                "setup_id": str(setup.id),
                "note": setup.note or "",
                "status": setup.status or "",
                "planted_in": str(setup.planted_in),
                "pay_off_in": str(setup.pay_off_in) if setup.pay_off_in else None,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class DeleteSetupTool(BaseTool):
    meta = ToolMeta(
        name="delete_setup",
        description="删除一条伏笔记录——建错了、建重了、那条伏笔根本不存在时用它。**不打算收了不要用这个**："
                    "把 status 改成 dropped（那也是一条信息）。删除之后 list_setups 里再也看不到它",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "setup_id": {"type": "string", "description": "要删除的伏笔ID，来自 list_setups"},
            },
            "required": ["setup_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            sid_raw = self._require_param(kwargs, "setup_id")
            if sid_raw is None:
                return self._missing_param("setup_id")
            setup = (await db.execute(
                select(Setup).where(Setup.id == uuid.UUID(sid_raw))
            )).scalar_one_or_none()
            if not setup:
                return self._not_found("Setup")
            await verify_project_owner(db, setup.project_id, kwargs.get("user_id"))
            # 先取出要回报的字段：commit 之后 ORM 对象已过期，读属性会再发一次查询。
            setup_id, note = setup.id, setup.note
            await db.delete(setup)
            await db.commit()
            return ToolResult(success=True, data={
                "deleted_setup_id": str(setup_id),
                "note": note or "",
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)
