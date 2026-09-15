"""invoke_skill tool — AI calls this to activate a skill.

When invoked, the skill's ``prompt_overrides`` are injected into the
system prompt on the next turn and its ``rag_tags`` are added to the
knowledge retrieval index.  Tools are NOT gated by skills — the mode
(chat / cowriter) controls tool availability.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.base import BaseTool, ToolMeta, ToolResult, ConcurrencyMode
from app.knowledge.skill_engine import _shared_engine as _skill_engine


class InvokeSkillTool(BaseTool):
    meta = ToolMeta(
        name="invoke_skill",
        description="启用一个写作技能。调用后系统在下一轮注入该技能的提示词。技能名见可用技能列表",
        parameters={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要启用的技能名称（如'悬疑推理'、'言情'、'网络爽文'、'现实主义'），见可用技能列表",
                },
            },
            "required": ["skill_name"],
        },
        concurrency=ConcurrencyMode.SAFE,
    )

    async def run(self, db: AsyncSession | None = None, **kwargs) -> ToolResult:
        skill_name = kwargs.get("skill_name", "").strip()
        if not skill_name:
            return ToolResult(success=False, error="请提供技能名称")

        data = await _skill_engine.get_skill(skill_name)
        if data is None:
            all_skills = await _skill_engine.get_all_skills_meta()
            names = [s["name"] for s in all_skills]
            return ToolResult(
                success=False,
                error=f"未找到技能 '{skill_name}'。可用技能：{', '.join(names)}",
            )

        display_name = data.get("name", skill_name)
        description = data.get("description", "")
        tags = data.get("rag_tags", [])

        return ToolResult(
            success=True,
            data={
                "skill_name": display_name,
                "description": description,
                "rag_tags": tags,
                "message": f"已启用技能「{display_name}」：{description}",
            },
        )
