from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.agent.consistency.checker import ConsistencyChecker


class ConsistencyCheckTool(BaseTool):
    """Long-running consistency check.

    A cold run takes minutes, so this tool opens its **own** session instead
    of using the executor's shared ``AsyncSession``. Combined with the
    ``_uses_own_session`` marker, the executor skips the shared SAFE lock for
    this tool — otherwise a minutes-long check would block every other SAFE
    tool in the loop (see 一致性分析引擎v2设计文档 §9.3).
    """

    _uses_own_session = True

    meta = ToolMeta(
        name="check_consistency",
        description="检查故事一致性，返回角色、情节、设定一致性报告（可能耗时数分钟，请耐心等待）",
        concurrency=ConcurrencyMode.SAFE,
        timeout=600,
        max_result_chars=6000,
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = self._require_param(kwargs, "project_id")
            if pid is None:
                return self._missing_param("project_id")
            import uuid
            pid = uuid.UUID(pid)
            # Ownership is verified on the tool's own session (the shared
            # session is deliberately not touched here).
            from app.database import async_session

            async with async_session() as own_db:
                await verify_project_owner(own_db, pid, kwargs.get("user_id"))
                checker = ConsistencyChecker(own_db)
                report = await checker.check_all(str(pid), requested_by=kwargs.get("user_id"))
                return ToolResult(success=True, data=report.model_dump(mode="json"))
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            try:
                await db.rollback()
            except Exception:
                pass
            return ToolResult(success=False, error=str(e))


class RhythmAnalyzeTool(BaseTool):
    meta = ToolMeta(
        name="analyze_rhythm",
        description="分析故事节奏（动作、悬疑、情感、幽默、强度），返回基本分析报告",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = self._require_param(kwargs, "project_id")
            if pid is None:
                return self._missing_param("project_id")
            import uuid
            pid = uuid.UUID(pid)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            from app.agent.rhythm.analyzer import RhythmAnalyzer
            analyzer = RhythmAnalyzer(db)
            result = await analyzer.analyze(pid)
            return ToolResult(success=True, data=result)
        except Exception as e:
            try:
                await db.rollback()
            except Exception:
                pass
            return ToolResult(success=False, error=str(e))
