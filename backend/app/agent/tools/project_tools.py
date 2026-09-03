from __future__ import annotations

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.project.models import Project, ProjectConfig
from app.storycad.models import Act, Chapter, Scene, SceneContent
from app.storycad.repository import AiStoryCADRepository
from app.project.repository import ProjectRepository
from app.utils import row_to_dict


class ReadProjectTool(BaseTool):
    meta = ToolMeta(
        name="read_project",
        description="加载项目元数据（标题、体裁、描述、配置），不包含幕/章/场景。获取完整结构请用 read_full_project",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            proj_repo = ProjectRepository(db)
            project = await proj_repo.get(pid)
            if not project:
                return self._not_found("Project")
            config = await proj_repo.get_config(pid)
            data = row_to_dict(project)
            if config:
                data["config"] = row_to_dict(config)
            return ToolResult(success=True, data=data)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ReadChapterTool(BaseTool):
    meta = ToolMeta(
        name="read_chapter",
        description="获取章节及其场景列表（章节ID可在 list_chapters 返回或结构概览 [ch:xxx] 中找到）",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 list_chapters 返回结果或 read_full_project 结构概览"},
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            ch_id = uuid.UUID(ch_raw)
            result = await db.execute(select(Chapter).where(Chapter.id == ch_id))
            chapter = result.scalar_one_or_none()
            if not chapter:
                return self._not_found("Chapter")
            await verify_project_owner(db, chapter.project_id, kwargs.get("user_id"))
            scenes_result = await db.execute(
                select(Scene).where(Scene.chapter_id == ch_id).order_by(Scene.sort_order)
            )
            scenes = [row_to_dict(s) for s in scenes_result.scalars().all()]
            data = row_to_dict(chapter)
            data["scenes"] = scenes
            return ToolResult(success=True, data=data)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ReadSceneTool(BaseTool):
    meta = ToolMeta(
        name="read_scene",
        description="获取场景蓝图与元数据（标题、蓝图、POV、地点、时间、是否已写正文）。scene_id 来自 list_scenes 或 read_full_project。默认不含正文——续写/分析以蓝图为依据即可；仅当需要做文字级编辑（expand_selection/compress_selection/rewrite 等）时才传 include_content=true 读取正文",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景ID，来自 list_scenes 返回结果或 read_full_project 结构概览"},
                "include_content": {"type": "boolean", "description": "是否包含场景正文本（默认 false）"},
            },
            "required": ["scene_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            sc_raw = self._require_param(kwargs, "scene_id")
            if sc_raw is None:
                return self._missing_param("scene_id")
            sc_id = uuid.UUID(sc_raw)
            result = await db.execute(select(Scene).where(Scene.id == sc_id))
            scene = result.scalar_one_or_none()
            if not scene:
                return self._not_found("Scene")
            await verify_project_owner(db, scene.project_id, kwargs.get("user_id"))
            data = row_to_dict(scene)
            include_content = bool(kwargs.get("include_content", False))
            content_result = await db.execute(select(SceneContent).where(SceneContent.scene_id == sc_id))
            sc_content = content_result.scalar_one_or_none()
            body = sc_content.content if sc_content else ""
            data["written"] = bool(body and body.strip())
            if include_content:
                data["content"] = body
            else:
                data["content"] = None
            return ToolResult(success=True, data=data)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class CreateSceneTool(BaseTool):
    meta = ToolMeta(
        name="create_scene",
        description="在指定章节中创建新场景，需提供章节ID和标题。chapter_id 来自 list_chapters 或 read_full_project",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "所属章节ID，来自 list_chapters 或 read_full_project"},
                "title": {"type": "string", "description": "场景标题"},
                "sort_order": {"type": "integer", "description": "排序序号"},
                "summary": {"type": "string", "description": "场景蓝图（创作计划：含【目标】【节拍】【关键信息】【结尾状态】）"},
                "content": {"type": "string", "description": "场景正文"},
                "pov_character": {"type": "string", "description": "POV角色"},
                "setting": {"type": "string", "description": "场景地点"},
                "scene_time": {"type": "string", "description": "场景时间"},
            },
            "required": ["chapter_id", "title"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            ch_id = uuid.UUID(kwargs["chapter_id"])
            # 安全：校验章节属于当前项目，防止跨项目创建场景（IDOR 写入）
            chapter = await db.get(Chapter, ch_id)
            if chapter is None or chapter.project_id != pid:
                return ToolResult(success=False, error="章节不存在或不属于该项目")
            repo = AiStoryCADRepository(db)
            scene_data = {
                "project_id": str(pid),
                "chapter_id": str(ch_id),
                "title": kwargs.get("title", "新场景"),
                "sort_order": kwargs.get("sort_order", 0),
                "summary": kwargs.get("summary", ""),
                "pov_character": kwargs.get("pov_character", ""),
                "setting": kwargs.get("setting", ""),
                "scene_time": kwargs.get("scene_time", ""),
            }
            content = kwargs.get("content")
            created = await repo.create_entity(Scene, scene_data)
            if content:
                sc_id = uuid.UUID(created["id"])
                db.add(SceneContent(scene_id=sc_id, project_id=pid, content=content))
                from app.agent.utils import count_words
                word_count = count_words(content)
                scene_obj = await db.get(Scene, sc_id)
                if scene_obj:
                    scene_obj.word_count = word_count
            await db.commit()
            return ToolResult(success=True, data=created)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class UpdateSceneTool(BaseTool):
    meta = ToolMeta(
        name="update_scene",
        description="更新场景内容、标题、POV、地点、时间、梗概等。scene_id 来自 list_scenes 或 read_full_project",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景ID，来自 list_scenes 或 read_full_project"},
                "title": {"type": "string", "description": "场景标题"},
                "summary": {"type": "string", "description": "场景蓝图（创作计划：含【目标】【节拍】【关键信息】【结尾状态】）"},
                "content": {"type": "string", "description": "场景正文"},
                "pov_character": {"type": "string", "description": "POV角色"},
                "setting": {"type": "string", "description": "场景地点"},
                "scene_time": {"type": "string", "description": "场景时间"},
            },
            "required": ["scene_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            sc_id = uuid.UUID(kwargs["scene_id"])
            scene_result = await db.get(Scene, sc_id)
            if scene_result:
                await verify_project_owner(db, scene_result.project_id, kwargs.get("user_id"))
            repo = AiStoryCADRepository(db)
            update_data = {"id": str(sc_id)}
            for field in ("title", "summary", "pov_character", "setting", "scene_time"):
                if field in kwargs:
                    update_data[field] = kwargs[field]
            updated = await repo.update_entity(Scene, update_data)
            if not updated:
                return self._not_found("Scene")
            if "content" in kwargs:
                content = kwargs["content"]
                result = await db.execute(select(SceneContent).where(SceneContent.scene_id == sc_id))
                sc = result.scalar_one_or_none()
                scene_obj = await db.get(Scene, sc_id)
                if sc:
                    sc.content = content
                elif scene_obj:
                    db.add(SceneContent(scene_id=sc_id, project_id=scene_obj.project_id, content=content))
                if scene_obj:
                    from app.agent.utils import count_words
                    scene_obj.word_count = count_words(content)
            await db.commit()
            return ToolResult(success=True, data=updated)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ReadProjectOverviewTool(BaseTool):
    meta = ToolMeta(
        name="read_project_overview",
        description="加载项目结构概览：幕/章/场景的树状骨架（仅名称和ID）、角色名和类型、主题名。不包含正文、性格描述、关系详情。这是了解项目全貌的入口工具",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            from app.agent.context import ContextBuilder
            builder = ContextBuilder(db)
            ctx = await builder.build_summary(pid, depth="minimal")
            return ToolResult(success=True, data=ctx)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ReadFullProjectTool(BaseTool):
    meta = ToolMeta(
        name="read_full_project",
        description="加载完整项目上下文，包括所有幕、章节、场景、角色、关系、主题",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            from app.agent.context import ContextBuilder
            builder = ContextBuilder(db)
            ctx = await builder.build_full(pid)
            return ToolResult(success=True, data=ctx)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class SetChapterGoalTool(BaseTool):
    meta = ToolMeta(
        name="set_chapter_goal",
        description="设置章节的创作蓝图（章级创作目标）",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 list_chapters 或 read_full_project"},
                "goal": {"type": "string", "description": "章节蓝图（含【章核心】【预期节拍】【情绪弧线】【结尾钩】【角色侧重】【主题浸染】）"},
            },
            "required": ["chapter_id", "goal"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            goal_raw = self._require_param(kwargs, "goal")
            if goal_raw is None:
                return self._missing_param("goal")
            ch_id = uuid.UUID(ch_raw)
            result = await db.execute(select(Chapter).where(Chapter.id == ch_id))
            ch = result.scalar_one_or_none()
            if ch:
                await verify_project_owner(db, ch.project_id, kwargs.get("user_id"))
            if not ch:
                return self._not_found("Chapter")
            ch.goal = goal_raw
            await db.commit()
            return ToolResult(success=True, data={"chapter_id": str(ch_id), "goal": goal_raw})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class UpdateChapterTool(BaseTool):
    meta = ToolMeta(
        name="update_chapter",
        description="更新章节信息（标题、状态、目标）。章节ID可在 list_chapters 或结构概览 [ch:xxx] 中找到",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 list_chapters 或 read_full_project"},
                "title": {"type": "string", "description": "章节标题"},
                "status": {"type": "string", "description": "状态：draft（草稿）/revising（修订中）/final（终稿）"},
                "goal": {"type": "string", "description": "章节蓝图（章级创作计划）"},
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            ch_id = uuid.UUID(ch_raw)
            result = await db.execute(select(Chapter).where(Chapter.id == ch_id))
            ch = result.scalar_one_or_none()
            if not ch:
                return self._not_found("Chapter")
            await verify_project_owner(db, ch.project_id, kwargs.get("user_id"))
            if "title" in kwargs:
                ch.title = kwargs["title"]
            if "status" in kwargs:
                valid_statuses = {"draft", "revising", "final"}
                if kwargs["status"] not in valid_statuses:
                    return ToolResult(
                        success=False,
                        error=f"无效状态 '{kwargs['status']}'，有效值为：{', '.join(sorted(valid_statuses))}",
                        correction_hint=f"请将 status 设为 draft（草稿）、revising（修订中）或 final（终稿）之一",
                    )
                ch.status = kwargs["status"]
            if "goal" in kwargs:
                ch.goal = kwargs["goal"]
            await db.commit()
            return ToolResult(success=True, data={"chapter_id": str(ch_id), "title": ch.title, "status": ch.status})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class UpdateActTool(BaseTool):
    meta = ToolMeta(
        name="update_act",
        description="更新幕信息（名称、颜色）",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "act_id": {"type": "string", "description": "幕ID，来自 read_full_project 结构概览"},
                "name": {"type": "string", "description": "幕名称"},
                "color": {"type": "string", "description": "颜色代码"},
            },
            "required": ["act_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            act_raw = self._require_param(kwargs, "act_id")
            if act_raw is None:
                return self._missing_param("act_id")
            act_id = uuid.UUID(act_raw)
            result = await db.execute(select(Act).where(Act.id == act_id))
            act = result.scalar_one_or_none()
            if not act:
                return self._not_found("Act in project")
            await verify_project_owner(db, act.project_id, kwargs.get("user_id"))
            if "name" in kwargs:
                act.name = kwargs["name"]
            if "color" in kwargs:
                act.color = kwargs["color"]
            await db.commit()
            return ToolResult(success=True, data={"act_id": str(act_id), "name": act.name})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))
