from __future__ import annotations

import logging
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.llm.client import LLMClient
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.agent.context import ContextBuilder
from app.agent.agents.writer_agent import WritingAgent
from app.agent.tools.writing_tools import WriteSceneContentTool
from app.storycad.models import Scene
from sqlalchemy import select

logger = logging.getLogger(__name__)


class CallWriterAgentTool(BaseTool):
    """调用专业写作智能体进行场景正文创作。

    工作流程：
    1. 通过 ContextBuilder.build_for_writing() 获取专注的写作上下文
    2. 调用 WritingAgent 生成正文（纯写作 prompt，无工具干扰）
    3. 直接保存到 DB，返回摘要给主 LLM（避免正文文本在主 LLM 上下文中膨胀）
    """

    meta = ToolMeta(
        name="call_writer_agent",
        description="调用专业写作智能体进行场景正文创作，支持新写、续写、重写。完成后自动保存。",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        timeout=180,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {
                    "type": "string",
                    "description": "场景ID，来自 list_scenes 或 read_full_project",
                },
                "action": {
                    "type": "string",
                    "enum": ["write", "continue", "rewrite"],
                    "description": "write=新写覆盖, continue=续写追加, rewrite=重写",
                },
                "instructions": {
                    "type": "string",
                    "description": "写作指导：字数要求、风格方向、重点内容等",
                },
            },
            "required": ["scene_id", "action"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            scene_id_raw = self._require_param(kwargs, "scene_id")
            if scene_id_raw is None:
                return self._missing_param("scene_id")
            action = kwargs.get("action")
            if action not in ("write", "continue", "rewrite"):
                return ToolResult(
                    success=False,
                    error="参数缺失: 工具需要 action 参数但未提供或取值非法（可选值：write/continue/rewrite）。不要省略该参数，否则将默认覆盖场景正文。",
                    correction_hint="下一步：明确指定 action=write（新写覆盖）/continue（续写追加）/rewrite（重写）后再调用本工具",
                )
            instructions = kwargs.get("instructions", "")

            sc_id = uuid.UUID(scene_id_raw)
            result = await db.execute(select(Scene).where(Scene.id == sc_id))
            scene_obj = result.scalar_one_or_none()
            if not scene_obj:
                return self._not_found("Scene")
            await verify_project_owner(db, scene_obj.project_id, kwargs.get("user_id"))

            # 1. 构建专注的写作上下文
            builder = ContextBuilder(db)
            ctx = await builder.build_for_writing(sc_id, action)

            # 2. 注入指令
            if instructions:
                ctx["instructions"] = instructions
            ctx["action"] = action

            # 3. 调用写作智能体
            agent = WritingAgent()
            user_prompt = f"请{action=='continue' and '续写' or action=='rewrite' and '重写' or '创作'}场景《{ctx.get('scene_title', '')}》的正文。"
            text = await agent.run(self.llm_client, ctx, user_prompt)

            if not text:
                return ToolResult(success=False, error="写作智能体未能生成正文")

            # 4. 保存到 DB
            writer = WriteSceneContentTool(llm_client=self.llm_client)
            save_result = await writer.run(
                db,
                user_id=kwargs.get("user_id"),
                scene_id=scene_id_raw,
                content=text,
            )

            if not save_result.success:
                return save_result

            preview = text[:200].replace("\n", " ")
            wc = save_result.data.get("word_count", 0)
            return ToolResult(
                success=True,
                data={
                    "scene_id": scene_id_raw,
                    "word_count": wc,
                    "action": action,
                    "preview": preview,
                    "summary": f"已{action=='continue' and '续写' or action=='rewrite' and '重写' or '创作'}场景《{ctx.get('scene_title', '')}》，共 {wc} 字",
                },
                correction_hint="如需调整内容，可以重新调用写作工具，并在指令中说明修改方向",
            )
        except Exception as e:
            await db.rollback()
            logger.error("CallWriterAgentTool failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=str(e))
