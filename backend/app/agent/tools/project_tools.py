"""作品层写入工具——规格第 9 节。

``update_project`` 管的是**当前已经确定的部分**：一句话梗概 / 当前已完成骨架 /
当前全局设定。骨架只覆盖已经创建出来的章（含还没写正文的章），续写时用
``spine_append`` 往后追加，不一次定完整本书，也不预写终局。
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
        description="改作品层：书名 / 类型 / 一句话梗概 / 当前已完成骨架 / 当前全局设定 / 目标字数，"
                    "只传要改的字段。**当前已完成骨架** = 目前已经确定、已经创建出来（含还没写正文）的这一段走向，"
                    "不是全书大纲；续写时用 spine_append 往后追加，不要整段重写。"
                    "**当前全局设定** = 目前已知的世界观与背景；补充时用 global_settings_append",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "书名"},
                "genre": {"type": "string", "description": "类型，例如：悬疑、奇幻、科幻"},
                "logline": {"type": "string", "description": "一句话梗概：这个故事是什么、开局的钩子"},
                "spine": {"type": "string", "description": "当前已完成骨架：只覆盖目前已经确定、已经创建的章（含还没写正文的章），写成一串因果节点。要往后扩写用 spine_append，不要整段覆盖"},
                "spine_append": {"type": "string", "description": "往后追加当前已完成骨架：续写时把下一段走向接在现有骨架后面，不覆盖旧内容"},
                "global_settings": {"type": "string", "description": "当前全局设定：目前已知的世界观与背景（地名、习俗、规则、来龙去脉）"},
                "global_settings_append": {"type": "string", "description": "往后追加当前全局设定：补充新设定时接在现有内容后面，不覆盖旧内容"},
                "total_words": {"type": "integer", "description": "全书目标字数"},
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

    @staticmethod
    def _append_text(current: str, addition: str) -> str:
        """把新内容接在旧内容后面；两边都保留，绝不覆盖。"""
        current = (current or "").rstrip()
        addition = (addition or "").strip()
        if not addition:
            return current
        if not current:
            return addition
        return f"{current}\n{addition}"

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

            updatable = {"title", "genre", "logline", "spine", "global_settings", "total_words"}
            appendable = {"spine_append": "spine", "global_settings_append": "global_settings"}
            given = set(kwargs) & (updatable | set(appendable))
            if not given:
                return ToolResult(
                    success=False,
                    error="未传入任何要更新的字段，请在调用时至少提供 "
                          "title / genre / logline / spine / spine_append / "
                          "global_settings / global_settings_append / total_words 中的一个",
                    correction_hint="请传入要修改的字段及其新值",
                )

            project = await db.get(Project, project_id)
            if not project:
                return self._not_found("Project")

            for field in updatable - {"total_words"}:
                if field in kwargs and kwargs[field] is not None:
                    setattr(project, field, kwargs[field])
            for append_field, target in appendable.items():
                if kwargs.get(append_field) is not None:
                    setattr(project, target, self._append_text(
                        getattr(project, target) or "", str(kwargs[append_field])
                    ))
            if kwargs.get("total_words") is not None:
                try:
                    project.total_words = int(kwargs["total_words"])
                except (TypeError, ValueError):
                    return ToolResult(success=False, error="total_words 必须是整数")

            await db.commit()
            settings = project.global_settings or ""
            spine = project.spine or ""
            return ToolResult(success=True, data={
                "project_id": str(project.id),
                "title": project.title or "",
                "genre": project.genre or "",
                "logline": project.logline or "",
                "spine": spine,
                "spine_chars": len(spine),
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
        description="从素材生成一个故事开头：初步全局设定 / 开头几幕几章 / 人物（含他想要什么、他从哪到哪）"
                    "/ 角色关系 / 当前已完成骨架 / 一句话梗概。**不生成正文**，也**不预写全书结局**；"
                    "后面的走向等需要时再往后追加。素材撑不起这部分时，返回值里的 truncated 会如实列出被削减了什么。素材至少 10 个字",
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
                "spine": "", "logline": "",
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
