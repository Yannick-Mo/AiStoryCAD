"""作品层写入工具——规格第 9 节。

``update_project`` 里的**终局**和**主线骨架**是这次重构新增的两个字段：
章蓝图答不出「结局是什么」、也答不出「我离结局还有多远」。没有这两个字段，
每一章都在盲写，模型也没有判断"这一章该往哪使劲"的依据。
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolMeta, ToolResult, verify_project_owner
from app.project.models import Project

logger = logging.getLogger(__name__)


class UpdateProjectTool(BaseTool):
    meta = ToolMeta(
        name="update_project",
        description="改作品层：书名 / 类型 / 一句话梗概 / 终局 / 主线骨架 / 全局设定 / 目标字数，"
                    "只传要改的字段。**终局** = 故事结束时局面变成什么样、主角变成什么样；**主线骨架** = 从开局到终局的因果主链——写章节之前这两个必须已经存在",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "书名"},
                "genre": {"type": "string", "description": "类型，例如：悬疑、奇幻、科幻"},
                "logline": {"type": "string", "description": "一句话梗概：这个故事是什么、开局的钩子"},
                "ending": {"type": "string", "description": "终局：故事结束时局面变成什么样、主角变成什么样"},
                "spine": {"type": "string", "description": "主线骨架：从开局到终局的因果主链，例如「奔丧 → 发现账本缺页 → 姑妈改口 → 旧债浮出 → 陈默留下」"},
                "global_settings": {"type": "string", "description": "全局设定：世界观与背景（地名、习俗、规则、来龙去脉）"},
                "total_words": {"type": "integer", "description": "目标字数"},
            },
        },
    )

    # flash 模型偶尔会把 global_settings 写成单数或 world_setting 之类的名字。
    PARAM_ALIASES = {
        "global_setting": "global_settings",
        "world_setting": "global_settings",
        "world_settings": "global_settings",
        "summary": "logline",
    }

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            project_id = uuid.UUID(pid_raw)
            await verify_project_owner(db, project_id, kwargs.get("user_id"))

            for wrong, correct in self.PARAM_ALIASES.items():
                if wrong in kwargs and correct not in kwargs:
                    kwargs[correct] = kwargs[wrong]
                    logger.warning("update_project: aliased param '%s' -> '%s'", wrong, correct)

            updatable = {"title", "genre", "logline", "ending", "spine", "global_settings", "total_words"}
            given = set(kwargs) & updatable
            if not given:
                return ToolResult(
                    success=False,
                    error="未传入任何要更新的字段，请在调用时至少提供 "
                          "title / genre / logline / ending / spine / global_settings / total_words 中的一个",
                    correction_hint="请传入要修改的字段及其新值",
                )

            project = await db.get(Project, project_id)
            if not project:
                return self._not_found("Project")

            for field in updatable - {"total_words"}:
                if field in kwargs and kwargs[field] is not None:
                    setattr(project, field, kwargs[field])
            if kwargs.get("total_words") is not None:
                try:
                    project.total_words = int(kwargs["total_words"])
                except (TypeError, ValueError):
                    return ToolResult(success=False, error="total_words 必须是整数")

            await db.commit()
            settings = project.global_settings or ""
            return ToolResult(success=True, data={
                "project_id": str(project.id),
                "title": project.title or "",
                "genre": project.genre or "",
                "logline": project.logline or "",
                "ending": project.ending or "",
                "ending_chars": len(project.ending or ""),
                "spine": project.spine or "",
                "global_settings_chars": len(settings),
                "total_words": int(project.total_words or 0),
                "updated_fields": sorted(given),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class CreateProjectFromMaterialTool(BaseTool):
    meta = ToolMeta(
        name="create_project_from_material",
        description="从素材一次生成完整项目：幕（含每一幕要干什么）/ 章（含每一章要写什么）/ 人物（含他想要什么、他从哪到哪）"
                    "/ 角色关系 / 终局与主线骨架 / 全局设定。**不生成正文**。素材撑不起完整结构时，"
                    "返回值里的 truncated 会如实列出被削减了什么。素材至少 10 个字",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        timeout=120,
        parameters={
            "type": "object",
            "properties": {
                "material": {"type": "string", "description": "创作素材：想要创作的故事内容，至少10个字"},
                "project_title": {"type": "string", "description": "书名"},
            },
            "required": ["material", "project_title"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            material = (kwargs.get("material") or "").strip()
            if len(material) < 10:
                return ToolResult(success=False, error="素材至少需要10个字来表达基本创意")
            if len(material) > 5000:
                return ToolResult(success=False, error="素材不能超过5000字")

            user_id = kwargs.get("user_id")
            if not user_id:
                return ToolResult(success=False, error="创建项目需要用户登录")

            from app.agent.project_creator.graph import run_pipeline
            from app.agent.project_creator.writer import write_project_from_state

            state = {
                "material": material,
                "project_title": (kwargs.get("project_title") or "未命名项目").strip(),
                "genre": "", "tone": "",
                "plot_summary": "", "world_elements": "", "global_settings": "",
                "ending": "", "spine": "", "logline": "",
                "acts": [], "chapters": [], "characters": [], "relations": [],
                "errors": [],
            }
            try:
                async for _ in run_pipeline(state):
                    pass
            except Exception as e:
                return ToolResult(success=False, error=f"项目生成失败: {e}")

            result = await write_project_from_state(db, state, uuid.UUID(user_id))
            await db.commit()
            result["project_title"] = state.get("project_title", "")
            return ToolResult(success=True, data=result)
        except Exception as e:
            await db.rollback()
            return self._err(e)
