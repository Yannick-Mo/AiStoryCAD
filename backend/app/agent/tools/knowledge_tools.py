from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.knowledge.rag import RAGEngine


class SearchKnowledgeTool(BaseTool):
    meta = ToolMeta(
        name="search_knowledge",
        description="搜索写作知识库，获取与当前创作相关的技巧和参考",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或写作问题（如'如何写打斗场景'、'悬疑伏笔技巧'）"},
                "genre": {"type": "string", "description": "体裁（可选，如'悬疑'、'科幻'，用于过滤知识库）"},
                "limit": {"type": "integer", "description": "返回结果数量"},
            },
            "required": ["query"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            if not kwargs.get("project_id"):
                # project_id 必填:缺失时不再放行,避免检索全局/他人知识
                return ToolResult(success=False, error="project_id is required")
            await verify_project_owner(db, kwargs["project_id"], kwargs.get("user_id"))
            engine = RAGEngine(db)
            result = await engine.retrieve_context(
                project_id=kwargs.get("project_id"),
                genre=kwargs.get("genre", ""),
                query=kwargs.get("query", ""),
                limit=kwargs.get("limit", 5),
            )
            return ToolResult(success=True, data=result)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))
