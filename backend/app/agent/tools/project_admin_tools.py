from __future__ import annotations

import logging
import uuid
from sqlalchemy import select, func

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from app.project.models import Project, ProjectConfig
from app.storycad.models import Act, Chapter, ChapterEdge, Character, CharacterRelation, Scene, SceneContent
from app.storycad.repository import StoryCADRepository
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.agent.project_creator.state import MaterialState
from app.utils import row_to_dict


class CreateActTool(BaseTool):
    meta = ToolMeta(
        name="create_act",
        description="在项目中创建新幕（Act）",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "幕名称，例如'第一幕'、'第二幕'"},
                "color": {"type": "string", "description": "颜色代码，例如'#8b5cf6'"},
            },
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            project_id = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, project_id, kwargs.get("user_id"))

            result = await db.execute(
                select(func.coalesce(func.max(Act.sort_order), -1))
                .where(Act.project_id == project_id)
            )
            max_order = result.scalar() or -1
            sort_order = max_order + 1

            repo = StoryCADRepository(db)
            created = await repo.create_entity(Act, {
                "project_id": str(project_id),
                "name": kwargs.get("name", "新幕"),
                "color": kwargs.get("color", "#8b5cf6"),
                "sort_order": sort_order,
            })
            await db.commit()
            return ToolResult(success=True, data=created)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class CreateChapterTool(BaseTool):
    meta = ToolMeta(
        name="create_chapter",
        description="在指定幕（Act）中创建新章节（Chapter），需提供幕ID。act_id 来自 read_full_project 结构概览",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "act_id": {"type": "string", "description": "所属幕ID，来自 read_full_project 结构概览"},
                "title": {"type": "string", "description": "章节标题"},
                "goal": {"type": "string", "description": "章节蓝图（章级创作计划：含【章核心】【预期节拍】【情绪弧线】【结尾钩】【角色侧重】【主题浸染】）"},
                "status": {"type": "string", "description": "状态：draft（草稿）/revising（修订中）/final（终稿）"},
            },
            "required": ["act_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            project_id = uuid.UUID(kwargs["project_id"])
            act_id = uuid.UUID(kwargs["act_id"])
            await verify_project_owner(db, project_id, kwargs.get("user_id"))

            act_result = await db.execute(
                select(Act).where(Act.id == act_id, Act.project_id == project_id)
            )
            if not act_result.scalar_one_or_none():
                return self._not_found("Act in project")

            result = await db.execute(
                select(func.coalesce(func.max(Chapter.sort_order), -1))
                .where(Chapter.project_id == project_id)
            )
            max_order = result.scalar() or -1
            sort_order = max_order + 1

            repo = StoryCADRepository(db)
            created = await repo.create_entity(Chapter, {
                "project_id": str(project_id),
                "act_id": str(act_id),
                "title": kwargs.get("title", "新章"),
                "goal": kwargs.get("goal", ""),
                "status": kwargs.get("status", "draft"),
                "sort_order": sort_order,
            })
            await db.commit()
            return ToolResult(success=True, data=created)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class DeleteSceneTool(BaseTool):
    meta = ToolMeta(
        name="delete_scene",
        description="删除指定场景（Scene），同时删除场景正文内容。scene_id 来自 list_scenes",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景ID，来自 list_scenes 或 read_full_project"},
            },
            "required": ["scene_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            scene_id = uuid.UUID(kwargs["scene_id"])
            project_id = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, project_id, kwargs.get("user_id"))

            scene = await db.get(Scene, scene_id)
            if not scene:
                return self._not_found("Scene")
            if scene.project_id != project_id:
                return self._permission_denied("场景")

            await db.execute(
                SceneContent.__table__.delete().where(SceneContent.scene_id == scene_id)
            )
            await db.delete(scene)
            await db.flush()
            await _recalc_chapter_counts(db, project_id)
            await db.commit()
            return ToolResult(success=True, data={"deleted_scene_id": kwargs["scene_id"]})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class DeleteChapterTool(BaseTool):
    meta = ToolMeta(
        name="delete_chapter",
        description="删除指定章节（Chapter），同时删除该章节下的所有场景和场景正文。chapter_id 来自 list_chapters",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 list_chapters 或 read_full_project"},
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            chapter_id = uuid.UUID(kwargs["chapter_id"])
            project_id = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, project_id, kwargs.get("user_id"))

            chapter = await db.get(Chapter, chapter_id)
            if not chapter:
                return self._not_found("Chapter")
            if chapter.project_id != project_id:
                return self._permission_denied("章节")

            scene_ids = await db.execute(
                select(Scene.id).where(Scene.chapter_id == chapter_id)
            )
            deleted_scene_ids = [str(sid) for (sid,) in scene_ids.all()]
            for sid in deleted_scene_ids:
                await db.execute(
                    SceneContent.__table__.delete().where(SceneContent.scene_id == uuid.UUID(sid))
                )
            await db.execute(Scene.__table__.delete().where(Scene.chapter_id == chapter_id))
            await db.delete(chapter)
            await db.flush()
            await _recalc_chapter_counts(db, project_id)
            await db.commit()
            return ToolResult(success=True, data={
                "deleted_chapter_id": kwargs["chapter_id"],
                "deleted_scene_ids": deleted_scene_ids,
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class DeleteActTool(BaseTool):
    meta = ToolMeta(
        name="delete_act",
        description="删除指定幕（Act），同时删除该幕下的所有章节和场景。act_id 来自 read_full_project",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "act_id": {"type": "string", "description": "幕ID，来自 read_full_project 结构概览"},
            },
            "required": ["act_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            act_id = uuid.UUID(kwargs["act_id"])
            project_id = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, project_id, kwargs.get("user_id"))

            act = await db.get(Act, act_id)
            if not act:
                return self._not_found("Act")
            if act.project_id != project_id:
                return self._permission_denied("幕")

            chapter_ids = await db.execute(
                select(Chapter.id).where(Chapter.act_id == act_id)
            )
            deleted_chapter_ids: list[str] = []
            deleted_scene_ids: list[str] = []
            for (chid,) in chapter_ids.all():
                deleted_chapter_ids.append(str(chid))
                scene_ids = await db.execute(
                    select(Scene.id).where(Scene.chapter_id == chid)
                )
                for (sid,) in scene_ids.all():
                    deleted_scene_ids.append(str(sid))
                    await db.execute(
                        SceneContent.__table__.delete().where(SceneContent.scene_id == sid)
                    )
                await db.execute(Scene.__table__.delete().where(Scene.chapter_id == chid))
            await db.execute(Chapter.__table__.delete().where(Chapter.act_id == act_id))
            await db.delete(act)
            await db.flush()
            await _recalc_chapter_counts(db, project_id)
            await db.commit()
            return ToolResult(success=True, data={
                "deleted_act_id": kwargs["act_id"],
                "deleted_chapter_ids": deleted_chapter_ids,
                "deleted_scene_ids": deleted_scene_ids,
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class UpdateProjectTool(BaseTool):
    meta = ToolMeta(
        name="update_project",
        description="更新项目全局设定。参数：title(标题)、description(描述)、genre(体裁)、global_settings(世界观设定)、logline(一句话梗概)、target_audience(目标读者)、total_words(目标字数)、template_type(模板)。只传入需要修改的字段",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "项目标题"},
                "description": {"type": "string", "description": "项目描述"},
                "genre": {"type": "string", "description": "故事体裁，例如'奇幻'、'科幻'、'悬疑'"},
                "global_settings": {"type": "string", "description": "世界观设定、背景设定等全局设定文本"},
                "logline": {"type": "string", "description": "一句话梗概/Logline"},
                "target_audience": {"type": "string", "description": "目标读者群体"},
                "total_words": {"type": "integer", "description": "目标总字数"},
                "template_type": {"type": "string", "description": "模板类型，例如'four_act'、'three_act'"},
            },
        },
    )

    PARAM_ALIASES = {
        "global_setting": "global_settings",
        "world_setting": "global_settings",
        "world_settings": "global_settings",
    }

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            project_id = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, project_id, kwargs.get("user_id"))

            for wrong, correct in self.PARAM_ALIASES.items():
                if wrong in kwargs and correct not in kwargs:
                    kwargs[correct] = kwargs[wrong]
                    logger.warning("update_project: aliased param '%s' -> '%s'", wrong, correct)

            updatable = {"title", "description", "genre", "global_settings", "logline",
                         "target_audience", "total_words", "template_type"}
            given = set(kwargs) & updatable
            if not given:
                return ToolResult(
                    success=False,
                    error="未传入任何要更新的字段。请在调用时至少提供 title/description/genre/global_settings 中的一个",
                    correction_hint="请传入要修改的字段及其新值",
                )

            project = await db.get(Project, project_id)
            if not project:
                return self._not_found("Project")

            for field in ("title", "description", "genre", "global_settings", "logline"):
                if field in kwargs:
                    setattr(project, field, kwargs[field])

            config_fields = {"target_audience", "total_words", "template_type"}
            if any(f in kwargs for f in config_fields):
                config = await db.execute(
                    select(ProjectConfig).where(ProjectConfig.project_id == project_id)
                )
                config_obj = config.scalar_one_or_none()
                if not config_obj:
                    config_obj = ProjectConfig(project_id=project_id)
                    db.add(config_obj)
                    await db.flush()
                for field in config_fields:
                    if field in kwargs:
                        setattr(config_obj, field, kwargs[field])

            await db.commit()
            gs = project.global_settings or ""
            if len(gs) > 2000:
                gs_preview = gs[:2000] + f"\n... [全长 {len(gs)} 字，已截断]"
            else:
                gs_preview = gs
            return ToolResult(success=True, data={
                "project_id": str(project_id),
                "title": project.title,
                "description": project.description,
                "genre": project.genre,
                "logline": project.logline or "",
                "global_settings": gs_preview,
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class CreateProjectFromMaterialTool(BaseTool):
    meta = ToolMeta(
        name="create_project_from_material",
        description="根据用户提供的创作素材，自动生成完整项目（包括幕、章、场景、角色、关系、世界观设定）。"
                    "返回新创建的项目ID。素材至少10个字",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        timeout=120,
        parameters={
            "type": "object",
            "properties": {
                "material": {"type": "string", "description": "用户的创作素材，描述想要创作的故事内容，至少10个字"},
                "project_title": {"type": "string", "description": "项目标题"},
            },
            "required": ["material", "project_title"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            material = kwargs.get("material", "").strip()
            if len(material) < 10:
                return ToolResult(success=False, error="素材至少需要10个字来表达基本创意")
            if len(material) > 5000:
                return ToolResult(success=False, error="素材不能超过5000字")

            user_id = kwargs.get("user_id")
            if not user_id:
                return ToolResult(success=False, error="创建项目需要用户登录")

            from app.agent.project_creator.graph import run_pipeline
            initial_state: MaterialState = {
                "material": material,
                "project_title": (kwargs.get("project_title") or "未命名项目").strip(),
                "genre": "", "tone": "", "characters_raw": [],
                "plot_summary": "", "world_elements": "",
                "acts": [], "estimated_words": 0, "scenes": [],
                "characters": [], "relations": [], "edges": [],
                "global_settings": "", "errors": [],
            }

            try:
                async for _ in run_pipeline(initial_state):
                    pass
            except Exception as e:
                return ToolResult(success=False, error=f"项目生成失败: {e}")

            final_state = initial_state

            project_id = await _write_new_project(db, final_state, uuid.UUID(user_id))
            return ToolResult(success=True, data={
                "project_id": str(project_id),
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


# ── Edge validation helpers ──────────────────────────────────────────────


async def _would_create_cycle(
    db: AsyncSession,
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    exclude_edge_id: uuid.UUID | None = None,
) -> bool:
    """Check if adding an edge source_id -> target_id would create a cycle.

    Uses DFS from target_id to see if we can reach source_id through
    existing edges (excluding exclude_edge_id if provided).
    """
    if source_id == target_id:
        return True

    query = select(ChapterEdge).where(ChapterEdge.project_id == project_id)
    if exclude_edge_id:
        query = query.where(ChapterEdge.id != exclude_edge_id)
    result = await db.execute(query)
    all_edges = result.scalars().all()

    adj: dict[uuid.UUID, list[uuid.UUID]] = {}
    for e in all_edges:
        adj.setdefault(e.source_id, []).append(e.target_id)

    adj.setdefault(source_id, []).append(target_id)

    visited = set()
    stack = [target_id]
    while stack:
        nid = stack.pop()
        if nid == source_id:
            return True
        if nid in visited:
            continue
        visited.add(nid)
        for nxt in adj.get(nid, []):
            stack.append(nxt)
    return False


async def _check_timeline_uniqueness(
    db: AsyncSession,
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    exclude_edge_id: uuid.UUID | None = None,
) -> str | None:
    """Check timeline uniqueness rules.

    Returns an error message if violated, None if OK.
    """
    query = select(ChapterEdge).where(
        ChapterEdge.project_id == project_id,
        ChapterEdge.edge_type == "timeline",
    )
    if exclude_edge_id:
        query = query.where(ChapterEdge.id != exclude_edge_id)
    result = await db.execute(query)
    existing = result.scalars().all()

    for e in existing:
        if e.source_id == source_id:
            return f"章节 {source_id} 已有一个出向 timeline 连线，每个章节只能有一个出向 timeline 连线"
        if e.target_id == target_id:
            return f"章节 {target_id} 已有一个入向 timeline 连线，每个章节只能有一个入向 timeline 连线"
    return None


class CreateEdgeTool(BaseTool):
    meta = ToolMeta(
        name="create_edge",
        description="在两个章节之间创建连线关系（ChapterEdge），用于表示时间线、因果、闪回等章节间关系。"
                    "source_id/target_id 来自 list_chapters 或 read_full_project。"
                    "注意：不能自连接、不能形成环、timeline 类型每个章节只能有一个入向和一个出向",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "源章节ID，来自 list_chapters 或 read_full_project"},
                "target_id": {"type": "string", "description": "目标章节ID，来自 list_chapters 或 read_full_project"},
                "edge_type": {
                    "type": "string",
                    "description": "timeline（时间线/顺序，每个章节只能有一个入向和一个出向）、causal（因果关系）、foreshadow（伏笔/呼应）、character（角色弧线延续）",
                    "enum": ["timeline", "causal", "foreshadow", "character"],
                },
                "label": {"type": "string", "description": "连线标签，例如'因→果'、'伏笔→回收'"},
                "source_handle": {"type": "string", "description": "源端手柄位置，例如's-r'（右）、's-l'（左）"},
                "target_handle": {"type": "string", "description": "目标端手柄位置，如't-l'（左）、't-r'（右）"},
            },
            "required": ["source_id", "target_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            project_id = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, project_id, kwargs.get("user_id"))

            src_id = uuid.UUID(kwargs["source_id"])
            tgt_id = uuid.UUID(kwargs["target_id"])
            edge_type = kwargs.get("edge_type", "timeline")

            # ── Validation ──
            if src_id == tgt_id:
                return ToolResult(success=False, error="不能创建自连接连线，源章节和目标章节不能相同")

            source_ch = await db.get(Chapter, src_id)
            if not source_ch or source_ch.project_id != project_id:
                return self._not_found("Chapter in project", f"源章节ID: {src_id}")
            target_ch = await db.get(Chapter, tgt_id)
            if not target_ch or target_ch.project_id != project_id:
                return self._not_found("Chapter in project", f"目标章节ID: {tgt_id}")

            existing = await db.execute(
                select(ChapterEdge).where(
                    ChapterEdge.project_id == project_id,
                    ChapterEdge.source_id == src_id,
                    ChapterEdge.target_id == tgt_id,
                )
            )
            if existing.scalar_one_or_none():
                return ToolResult(success=False, error="这两个章节之间已存在连线")

            if await _would_create_cycle(db, project_id, src_id, tgt_id):
                return ToolResult(
                    success=False,
                    error="创建该连线会导致章节间形成循环依赖，请检查连线方向",
                    correction_hint="请尝试反转连线方向（交换 source_id 和 target_id），或检查章节间是否有其他路径形成了环路",
                )

            if edge_type == "timeline":
                err = await _check_timeline_uniqueness(db, project_id, src_id, tgt_id)
                if err:
                    return ToolResult(success=False, error=err)

            repo = StoryCADRepository(db)
            created = await repo.create_entity(ChapterEdge, {
                "project_id": str(project_id),
                "source_id": str(src_id),
                "target_id": str(tgt_id),
                "edge_type": edge_type,
                "label": kwargs.get("label", ""),
                "source_handle": kwargs.get("source_handle", "s-r"),
                "target_handle": kwargs.get("target_handle", "t-l"),
            })
            await db.commit()
            return ToolResult(success=True, data=created)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class UpdateEdgeTool(BaseTool):
    meta = ToolMeta(
        name="update_edge",
        description="更新章节连线的类型、标签或手柄位置。注意：改为 timeline 类型时会校验唯一性",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "edge_id": {"type": "string", "description": "连线ID，来自 list_edges 返回结果"},
                "edge_type": {
                    "type": "string",
                    "description": "timeline（时间线/顺序）、causal（因果关系）、foreshadow（伏笔/呼应）、character（角色弧线延续）",
                    "enum": ["timeline", "causal", "foreshadow", "character"],
                },
                "label": {"type": "string", "description": "连线标签"},
                "source_handle": {"type": "string", "description": "源端手柄位置"},
                "target_handle": {"type": "string", "description": "目标端手柄位置"},
            },
            "required": ["edge_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            project_id = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, project_id, kwargs.get("user_id"))

            edge_id = uuid.UUID(kwargs["edge_id"])
            edge = await db.get(ChapterEdge, edge_id)
            if not edge or edge.project_id != project_id:
                return self._not_found("Edge in project")

            new_type = kwargs.get("edge_type")
            if new_type is not None and new_type != edge.edge_type:
                if new_type == "timeline":
                    err = await _check_timeline_uniqueness(
                        db, project_id, edge.source_id, edge.target_id, exclude_edge_id=edge_id
                    )
                    if err:
                        return ToolResult(success=False, error=err)

            for field in ("edge_type", "label", "source_handle", "target_handle"):
                if field in kwargs:
                    setattr(edge, field, kwargs[field])
            await db.commit()
            return ToolResult(success=True, data={
                "edge_id": str(edge_id),
                "edge_type": edge.edge_type,
                "label": edge.label,
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class DeleteEdgeTool(BaseTool):
    meta = ToolMeta(
        name="delete_edge",
        description="删除指定章节连线。edge_id 来自 list_edges",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "edge_id": {"type": "string", "description": "连线ID，来自 list_edges 返回结果"},
            },
            "required": ["edge_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            project_id = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, project_id, kwargs.get("user_id"))
            edge_id = uuid.UUID(kwargs["edge_id"])
            edge = await db.get(ChapterEdge, edge_id)
            if not edge or edge.project_id != project_id:
                return self._not_found("Edge in project")
            await db.delete(edge)
            await db.commit()
            return ToolResult(success=True, data={"deleted_edge_id": kwargs["edge_id"]})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


async def _recalc_chapter_counts(db: AsyncSession, project_id: uuid.UUID) -> None:
    from app.storycad.repository import StoryCADRepository
    await StoryCADRepository(db)._recalc_chapter_counts(project_id)


async def _write_new_project(
    db: AsyncSession,
    state: dict,
    owner_id: uuid.UUID,
    do_commit: bool = True,
) -> uuid.UUID:
    repo = StoryCADRepository(db)
    project_title = state.get("project_title", "未命名项目")
    project = Project(
        title=project_title[:255],
        description=state.get("description", state.get("plot_summary", "")),
        owner_id=owner_id,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    project_id = project.id

    act_id_map: dict[int, str] = {}
    for act_idx, act in enumerate(state.get("acts", [])):
        result = await repo.create_entity(Act, {
            "project_id": str(project_id),
            "name": act.get("name", ""),
            "sort_order": act.get("order", act_idx + 1),
            "color": act.get("color", "#8b5cf6"),
        })
        act_id_map[act_idx] = result["id"]

    chapter_sort = 0
    chap_id_map: dict[tuple[int, int], str] = {}
    for act_idx, act in enumerate(state.get("acts", [])):
        for ch_idx, ch in enumerate(act.get("chapters", [])):
            chapter_sort += 1
            act_id = act_id_map.get(act_idx, "")
            chapter_result = await repo.create_entity(Chapter, {
                "project_id": str(project_id),
                "act_id": str(act_id),
                "title": ch.get("title", ""),
                "goal": ch.get("goal", ""),
                "sort_order": chapter_sort,
                "status": "draft",
            })
            chap_id_map[(act_idx, ch_idx)] = chapter_result["id"]

    scene_sort_total = 0
    for sc in sorted(
        state.get("scenes", []),
        key=lambda s: (s.get("act_idx", 0), s.get("chapter_idx", 0)),
    ):
        cid = chap_id_map.get((sc.get("act_idx", 0), sc.get("chapter_idx", 0)))
        if not cid:
            continue
        scene_sort_total += 1
        await repo.create_entity(Scene, {
            "project_id": str(project_id),
            "chapter_id": str(cid),
            "title": sc["title"],
            "pov_character": sc.get("pov_character", ""),
            "setting": sc.get("setting", ""),
            "scene_time": sc.get("scene_time", ""),
            "summary": sc.get("summary", ""),
            "sort_order": scene_sort_total,
        })

    char_name_to_id = {}
    for char in state.get("characters", []):
        result = await repo.create_entity(Character, {
            "project_id": str(project_id),
            "name": char["name"],
            "role": char.get("role", "supporting"),
            "personality": char.get("personality", ""),
            "appearance": char.get("appearance", ""),
            "background": char.get("background", ""),
            "motivation": char.get("motivation", ""),
        })
        char_name_to_id[char["name"]] = result["id"]

    for rel in state.get("relations", []):
        src_id = char_name_to_id.get(rel.get("char_name", ""))
        tgt_id = char_name_to_id.get(rel.get("target_name", ""))
        if src_id and tgt_id:
            await repo.create_entity(CharacterRelation, {
                "project_id": str(project_id),
                "character_id": str(src_id),
                "target_id": str(tgt_id),
                "rel_type": rel.get("rel_type", "关联"),
                "label": rel.get("label", ""),
                "description": rel.get("description", ""),
            })

    for edge in state.get("edges", []):
        src = chap_id_map.get(
            (edge.get("source_act_idx", 0), edge.get("source_chapter_idx", 0))
        )
        tgt = chap_id_map.get(
            (edge.get("target_act_idx", 0), edge.get("target_chapter_idx", 0))
        )
        if src and tgt:
            await repo.create_entity(ChapterEdge, {
                "project_id": str(project_id),
                "source_id": str(src),
                "target_id": str(tgt),
                "edge_type": edge.get("type", "timeline"),
                "label": edge.get("label", ""),
                "source_handle": "s-r",
                "target_handle": "t-l",
            })

    config = ProjectConfig(
        project_id=project_id,
        total_words=state.get("estimated_words", 50000),
        template_type="custom",
    )
    db.add(config)

    gs = state.get("global_settings", "")
    if gs:
        project.global_settings = gs

    if do_commit:
        await db.commit()
    return project_id
