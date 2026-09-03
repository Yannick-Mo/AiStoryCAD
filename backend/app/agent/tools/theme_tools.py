from __future__ import annotations

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.storycad.models import Theme, ThemeChapter, Chapter
from app.storycad.repository import AiStoryCADRepository
from app.utils import row_to_dict


class CreateThemeTool(BaseTool):
    meta = ToolMeta(
        name="create_theme",
        description="创建新主题，需提供主题名称",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "主题名称"},
                "proposition": {"type": "string", "description": "主题命题（如「爱能战胜一切」）"},
                "note": {"type": "string", "description": "主题备注"},
                "color": {"type": "string", "description": "颜色代码（如 #d4a373）"},
                "sort_order": {"type": "integer", "description": "排序序号"},
            },
            "required": ["name"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            repo = AiStoryCADRepository(db)
            data = {
                "project_id": str(pid),
                "name": kwargs["name"],
                "proposition": kwargs.get("proposition", ""),
                "note": kwargs.get("note", ""),
                "color": kwargs.get("color", "#d4a373"),
                "sort_order": kwargs.get("sort_order", 0),
            }
            created = await repo.create_entity(Theme, data)
            await db.commit()
            return ToolResult(success=True, data=created)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class UpdateThemeTool(BaseTool):
    meta = ToolMeta(
        name="update_theme",
        description="更新主题信息（名称、命题、备注、颜色）。theme_id 来自 read_full_project 结构概览",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "description": "主题ID，来自 read_full_project 结构概览中的主题列表"},
                "name": {"type": "string", "description": "主题名称"},
                "proposition": {"type": "string", "description": "主题命题"},
                "note": {"type": "string", "description": "主题备注"},
                "color": {"type": "string", "description": "颜色代码"},
            },
            "required": ["theme_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            theme_id = uuid.UUID(kwargs["theme_id"])
            result = await db.execute(select(Theme).where(Theme.id == theme_id))
            theme = result.scalar_one_or_none()
            if not theme:
                return self._not_found("Theme")
            await verify_project_owner(db, theme.project_id, kwargs.get("user_id"))
            repo = AiStoryCADRepository(db)
            data = {"id": str(theme_id)}
            for field in ("name", "proposition", "note", "color"):
                if field in kwargs:
                    data[field] = kwargs[field]
            updated = await repo.update_entity(Theme, data)
            if not updated:
                return self._not_found("Theme")
            await db.commit()
            return ToolResult(success=True, data=updated)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class DeleteThemeTool(BaseTool):
    meta = ToolMeta(
        name="delete_theme",
        description="删除指定主题。theme_id 来自 read_full_project 结构概览",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "description": "主题ID，来自 read_full_project 结构概览中的主题列表"},
            },
            "required": ["theme_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            theme_id = uuid.UUID(kwargs["theme_id"])
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            theme = await db.get(Theme, theme_id)
            if not theme:
                return self._not_found("Theme")
            if theme.project_id != pid:
                return self._permission_denied("主题")
            # Delete theme-chapter links first
            links = (await db.execute(
                select(ThemeChapter).where(ThemeChapter.theme_id == theme_id)
            )).scalars().all()
            for link in links:
                await db.delete(link)
            name = theme.name
            await db.delete(theme)
            await db.commit()
            return ToolResult(success=True, data={"deleted": name, "theme_id": str(theme_id)})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class LinkThemeChapterTool(BaseTool):
    meta = ToolMeta(
        name="link_theme_chapter",
        description="将主题关联到章节（添加主题-章节连线）",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "description": "主题ID，来自 read_full_project 结构概览"},
                "chapter_id": {"type": "string", "description": "章节ID，来自 list_chapters 或 read_full_project"},
            },
            "required": ["theme_id", "chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            theme_id = uuid.UUID(kwargs["theme_id"])
            chapter_id = uuid.UUID(kwargs["chapter_id"])

            theme = await db.get(Theme, theme_id)
            if not theme or theme.project_id != pid:
                return self._not_found("Theme in project")
            chapter = await db.get(Chapter, chapter_id)
            if not chapter or chapter.project_id != pid:
                return self._not_found("Chapter in project")

            # Check if link already exists
            existing = (await db.execute(
                select(ThemeChapter).where(
                    ThemeChapter.theme_id == theme_id,
                    ThemeChapter.chapter_id == chapter_id,
                )
            )).scalar_one_or_none()
            if existing:
                return ToolResult(success=False, error="Theme is already linked to this chapter")

            link = ThemeChapter(
                theme_id=theme_id,
                chapter_id=chapter_id,
                project_id=pid,
            )
            db.add(link)
            await db.commit()
            return ToolResult(success=True, data={
                "theme_id": str(theme_id),
                "chapter_id": str(chapter_id),
                "theme_name": theme.name,
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class UnlinkThemeChapterTool(BaseTool):
    meta = ToolMeta(
        name="unlink_theme_chapter",
        description="取消主题与章节的关联",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "theme_id": {"type": "string", "description": "主题ID，来自 read_full_project 结构概览"},
                "chapter_id": {"type": "string", "description": "章节ID，来自 list_chapters 或 read_full_project"},
            },
            "required": ["theme_id", "chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            theme_id = uuid.UUID(kwargs["theme_id"])
            chapter_id = uuid.UUID(kwargs["chapter_id"])

            link = (await db.execute(
                select(ThemeChapter).where(
                    ThemeChapter.theme_id == theme_id,
                    ThemeChapter.chapter_id == chapter_id,
                    ThemeChapter.project_id == pid,
                )
            )).scalar_one_or_none()
            if not link:
                return ToolResult(success=False, error="主题-章节关联不存在")

            await db.delete(link)
            await db.commit()
            return ToolResult(success=True, data={"unlinked": f"{theme_id} ↔ {chapter_id}"})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class SetChapterRhythmTool(BaseTool):
    meta = ToolMeta(
        name="set_chapter_rhythm",
        description="设置章节的节奏数值（动作/悬疑/情感/幽默/强度，0-10分）",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 list_chapters 或 read_full_project"},
                "action": {"type": "integer", "description": "动作（0-10）", "minimum": 0, "maximum": 10},
                "suspense": {"type": "integer", "description": "悬疑（0-10）", "minimum": 0, "maximum": 10},
                "emotion": {"type": "integer", "description": "情感（0-10）", "minimum": 0, "maximum": 10},
                "humor": {"type": "integer", "description": "幽默（0-10）", "minimum": 0, "maximum": 10},
                "intensity": {"type": "integer", "description": "强度（0-10）", "minimum": 0, "maximum": 10},
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            from app.storycad.models import ChapterRhythm

            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            ch_id = uuid.UUID(kwargs["chapter_id"])

            chapter = await db.get(Chapter, ch_id)
            if not chapter or chapter.project_id != pid:
                return self._not_found("Chapter in project")

            # Get existing or create new
            existing = (await db.execute(
                select(ChapterRhythm).where(ChapterRhythm.chapter_id == ch_id)
            )).scalar_one_or_none()

            if existing:
                for axis in ("action", "suspense", "emotion", "humor", "intensity"):
                    if axis in kwargs:
                        setattr(existing, axis, kwargs[axis])
            else:
                rhythm = ChapterRhythm(
                    chapter_id=ch_id,
                    project_id=pid,
                    action=kwargs.get("action", 5),
                    suspense=kwargs.get("suspense", 5),
                    emotion=kwargs.get("emotion", 5),
                    humor=kwargs.get("humor", 5),
                    intensity=kwargs.get("intensity", 5),
                )
                db.add(rhythm)

            await db.commit()
            return ToolResult(success=True, data={
                "chapter_id": str(ch_id),
                "action": kwargs.get("action", 5),
                "suspense": kwargs.get("suspense", 5),
                "emotion": kwargs.get("emotion", 5),
                "humor": kwargs.get("humor", 5),
                "intensity": kwargs.get("intensity", 5),
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))
