from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ContextBuilder
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner


class ReadChaptersTool(BaseTool):
    meta = ToolMeta(
        name="read_chapters",
        description="按全局章节序号读取一个连续范围的章节（含每章标题、状态、目标全文，不含场景及其目标/正文）。"
                    "全局章序号 = 全项目从第1幕第1章起连续编号（跨幕连续，第1幕第50章之后是第2幕第51章）。"
                    "例如想看第3到第7章：chapter_from=3、chapter_to=7。"
                    "每章返回 act_id/act_name；要看某章内的场景请改用 read_chapter。"
                    "返回 truncated=true 时用 next_from 继续翻页。"
                    "总章数与最新进度可用 read_recent 或项目框架了解",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        max_result_chars=12000,
        parameters={
            "type": "object",
            "properties": {
                "chapter_from": {"type": "integer", "description": "全局章序号起点（从1开始，含）"},
                "chapter_to": {"type": "integer", "description": "全局章序号终点（含）"},
                "include_goals": {
                    "type": "boolean",
                    "description": "是否包含每章目标全文（默认 true；只想扫标题/找ID可传 false 更省空间）",
                },
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

            builder = ContextBuilder(db)
            include_goals = bool(kwargs.get("include_goals", True))
            data = await builder.build_chapter_window(
                pid, chapter_from, chapter_to, include_goals=include_goals
            )
            return ToolResult(success=True, data=data)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ReadRecentTool(BaseTool):
    meta = ToolMeta(
        name="read_recent",
        description="读取最近写入/更新的场景或章节（按正文更新时间倒序，kind=scenes 返回场景快照含蓝图前500字，"
                    "kind=chapters 返回这些场景所在章含章目标全文）。用于了解项目最新进展与动向。"
                    "场景/章节ID 可从返回项中取得；要读完整正文/蓝图请用 read_scene/read_chapter",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "返回类型：scenes（最近写入的场景）/ chapters（最近写入场景所在章节），默认 scenes",
                    "enum": ["scenes", "chapters"],
                },
                "n": {"type": "integer", "description": "返回条数（1-10，默认5）"},
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

            kind = kwargs.get("kind") or "scenes"
            if kind not in ("scenes", "chapters"):
                return ToolResult(success=False, error="kind 仅支持 scenes 或 chapters")
            try:
                n = int(kwargs.get("n") or 5)
            except (TypeError, ValueError):
                n = 5
            n = max(1, min(n, 10))

            builder = ContextBuilder(db)
            data = await builder.build_recent_items(pid, kind=kind, n=n)
            return ToolResult(success=True, data=data)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))
