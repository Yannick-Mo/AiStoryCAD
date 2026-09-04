from __future__ import annotations

import uuid
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.storycad.models import Chapter, Scene, SceneContent
from app.storycad.repository import AiStoryCADRepository
from app.agent.utils import count_words


class RecalcWordCountsTool(BaseTool):
    meta = ToolMeta(
        name="recalc_word_counts",
        description="重算项目所有场景和章节的字数。读取每个场景的正文内容重新计算字数，"
                    "然后汇总更新每个章节的总字数。project_id 来自 read_project（项目元数据）",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "项目ID，来自项目框架结构概览或 read_chapters",
                },
            },
            "required": ["project_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            project_id_raw = self._require_param(kwargs, "project_id")
            if project_id_raw is None:
                return self._missing_param("project_id")

            pid = uuid.UUID(project_id_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            scenes_result = await db.execute(
                select(Scene.id).where(Scene.project_id == pid)
            )
            scene_ids = [row[0] for row in scenes_result.all()]

            updated = 0
            for sid in scene_ids:
                content_result = await db.execute(
                    select(SceneContent.content).where(SceneContent.scene_id == sid)
                )
                content_row = content_result.one_or_none()
                wc = count_words(content_row[0]) if content_row else 0
                await db.execute(
                    Scene.__table__.update().where(Scene.id == sid).values(word_count=wc)
                )
                if content_row:
                    updated += 1

            await AiStoryCADRepository(db)._recalc_chapter_counts(pid)

            chapters_result = await db.execute(
                select(Chapter.id, Chapter.title, func.coalesce(Chapter.total_words, 0))
                .where(Chapter.project_id == pid)
                .order_by(Chapter.sort_order)
            )
            chapters = [
                {"id": str(row[0]), "title": row[1], "word_count": row[2]}
                for row in chapters_result.all()
            ]
            project_total = sum(ch["word_count"] for ch in chapters)

            await db.commit()

            return ToolResult(success=True, data={
                "project_id": project_id_raw,
                "total_word_count": project_total,
                "chapters": chapters,
                "scenes_recalculated": updated,
                "scenes_total": len(scene_ids),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)
