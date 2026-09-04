from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ContextBuilder
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner


class ReadChaptersTool(BaseTool):
    meta = ToolMeta(
        name="read_chapters",
        description="按全局章节序号读取一个连续范围的章节，每章含：全局序号/ID/标题/状态/所属幕与幕ID/目标全文。"
                    "全局章序号 = 全项目从第1幕第1章起连续编号（跨幕连续，幕索引标了每幕章数可换算）。"
                    "例如想看第3到第7章：chapter_from=3、chapter_to=7。"
                    "用法建议：按当前创作实际依赖取离散区间（如同时依赖第1-3章与第15-20章就分别调 read_chapters(1,3)"
                    "与 read_chapters(15,20)），不要一次读很长的连续范围——窗口越大后半越可能被截断"
                    "（返回 truncated=true 时用 next_from 继续翻页）。"
                    "章内场景的轻量清单请用 read_chapter_scenes；场景蓝图/正文用 read_scene / read_scene_content。"
                    "最新进度可用 read_recent_scenes / read_recent_chapters 了解",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        max_result_chars=22000,
        parameters={
            "type": "object",
            "properties": {
                "chapter_from": {"type": "integer", "description": "全局章序号起点（从1开始，含）"},
                "chapter_to": {"type": "integer", "description": "全局章序号终点（含）"},
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
            data = await builder.build_chapter_window(pid, chapter_from, chapter_to)
            return ToolResult(success=True, data=data)
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadRecentScenesTool(BaseTool):
    meta = ToolMeta(
        name="read_recent_scenes",
        description="读取最近写入/更新的场景（按正文更新时间倒序）：每项 = 场景ID/标题/所在章与幕/字数/更新时间/蓝图前500字。"
                    "用于了解最新写到哪、拿最近场景的 ID。要完整蓝图/正文请用 read_scene / read_scene_content",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        parameters={
            "type": "object",
            "properties": {
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
            try:
                n = int(kwargs.get("n") or 5)
            except (TypeError, ValueError):
                n = 5
            n = max(1, min(n, 10))
            builder = ContextBuilder(db)
            data = await builder.build_recent_items(pid, kind="scenes", n=n)
            return ToolResult(success=True, data=data)
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadRecentChaptersTool(BaseTool):
    meta = ToolMeta(
        name="read_recent_chapters",
        description="读取最近被写入的章节（按其中场景的正文更新时间倒序，去重）：每项 = 章节ID/标题/状态/目标全文/"
                    "最新写入场景标题与时间。用于快速了解最近写作围绕哪些章。"
                    "场景级动态请看 read_recent_scenes",
        concurrency=ConcurrencyMode.SAFE,
        timeout=30,
        parameters={
            "type": "object",
            "properties": {
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
            try:
                n = int(kwargs.get("n") or 5)
            except (TypeError, ValueError):
                n = 5
            n = max(1, min(n, 10))
            builder = ContextBuilder(db)
            data = await builder.build_recent_items(pid, kind="chapters", n=n)
            return ToolResult(success=True, data=data)
        except Exception as e:
            await db.rollback()
            return self._err(e)
