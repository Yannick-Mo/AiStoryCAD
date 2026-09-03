import uuid
from sqlalchemy import select, func
from app.mcp.server import mcp
from app.database import async_session
from app.storycad.models import Chapter, Scene, SceneContent
from app.storycad.repository import AiStoryCADRepository
from app.utils import row_to_dict
from app.mcp.auth import get_current_user_mcp, verify_project_ownership


@mcp.tool()
async def read_chapter(token: str, chapter_id: str) -> dict:
    """获取章节及其包含的场景列表"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        result = await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
        chapter = result.scalar_one_or_none()
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")
        await verify_project_ownership(str(chapter.project_id), user["id"], db)
        scenes_result = await db.execute(
            select(Scene).where(Scene.chapter_id == uuid.UUID(chapter_id)).order_by(Scene.sort_order)
        )
        scenes = [row_to_dict(s) for s in scenes_result.scalars().all()]
        data = row_to_dict(chapter)
        data["scenes"] = scenes
        return data


@mcp.tool()
async def read_scene(token: str, scene_id: str) -> dict:
    """获取场景内容，包含正文"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        result = await db.execute(select(Scene).where(Scene.id == uuid.UUID(scene_id)))
        scene = result.scalar_one_or_none()
        if not scene:
            raise ValueError(f"Scene {scene_id} not found")
        await verify_project_ownership(str(scene.project_id), user["id"], db)
        content_result = await db.execute(
            select(SceneContent).where(SceneContent.scene_id == uuid.UUID(scene_id))
        )
        sc = content_result.scalar_one_or_none()
        data = row_to_dict(scene)
        data["content"] = sc.content if sc else ""
        return data


@mcp.tool()
async def create_scene(
    token: str,
    project_id: str,
    chapter_id: str,
    title: str,
    summary: str = "",
    content: str = "",
    pov_character: str = "",
    setting: str = "",
    scene_time: str = "",
    sort_order: int = 0,
) -> dict:
    """在指定章节下创建新场景"""
    from app.agent.utils import count_words

    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        # 安全：章节必须属于当前项目，否则场景会挂到他人章节下并污染其计数。
        chapter_result = await db.execute(
            select(Chapter).where(Chapter.id == uuid.UUID(chapter_id))
        )
        chapter = chapter_result.scalar_one_or_none()
        if chapter is None or str(chapter.project_id) != str(project_id):
            raise ValueError("章节不属于该项目")
        repo = AiStoryCADRepository(db)
        scene_data = {
            "project_id": project_id,
            "chapter_id": chapter_id,
            "title": title,
            "sort_order": sort_order,
            "summary": summary,
            "pov_character": pov_character,
            "setting": setting,
            "scene_time": scene_time,
        }
        created = await repo.create_entity(Scene, scene_data)
        try:
            if content:
                sc_id = uuid.UUID(created["id"])
                db.add(SceneContent(scene_id=sc_id, project_id=uuid.UUID(project_id), content=content))
                result = await db.execute(select(Scene).where(Scene.id == sc_id))
                scene_obj = result.scalar_one_or_none()
                if scene_obj:
                    scene_obj.word_count = count_words(content)
                await db.flush()
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return created


@mcp.tool()
async def update_scene(
    token: str,
    scene_id: str,
    title: str | None = None,
    summary: str | None = None,
    content: str | None = None,
    pov_character: str | None = None,
    setting: str | None = None,
    scene_time: str | None = None,
) -> dict:
    """更新场景内容、标题、POV、地点、时间、梗概等"""
    from app.agent.utils import count_words

    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        result = await db.execute(select(Scene).where(Scene.id == uuid.UUID(scene_id)))
        scene = result.scalar_one_or_none()
        if not scene:
            raise ValueError(f"Scene {scene_id} not found")
        await verify_project_ownership(str(scene.project_id), user["id"], db)
        repo = AiStoryCADRepository(db)
        update_data = {"id": scene_id}
        for field in ("title", "summary", "pov_character", "setting", "scene_time"):
            val = locals()[field]
            if val is not None:
                update_data[field] = val
        updated = await repo.update_entity(Scene, update_data)
        try:
            if content is not None:
                sid = uuid.UUID(scene_id)
                result = await db.execute(select(SceneContent).where(SceneContent.scene_id == sid))
                sc = result.scalar_one_or_none()
                if sc:
                    sc.content = content
                else:
                    result = await db.execute(select(Scene).where(Scene.id == sid))
                    scene_obj = result.scalar_one_or_none()
                    if scene_obj:
                        db.add(SceneContent(scene_id=sid, project_id=scene_obj.project_id, content=content))
                result = await db.execute(select(Scene).where(Scene.id == sid))
                scene_obj = result.scalar_one_or_none()
                if scene_obj:
                    scene_obj.word_count = count_words(content)
                await db.flush()
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return updated


@mcp.tool()
async def recalc_project_word_counts(token: str, project_id: str) -> dict:
    """重算项目所有场景和章节的字数。读取每个场景的正文内容用 count_words() 重新计数字数，
    然后汇总更新每个章节的总字数。已经写了正文的场景会重新计算字数，从未写过正文的场景保持 0。"""
    from app.agent.utils import count_words

    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)

        pid = uuid.UUID(project_id)

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

        repo = AiStoryCADRepository(db)
        await repo._recalc_chapter_counts(pid)

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

        return {
            "project_id": project_id,
            "total_word_count": project_total,
            "chapters": chapters,
            "scenes_recalculated": updated,
            "scenes_total": len(scene_ids),
        }
