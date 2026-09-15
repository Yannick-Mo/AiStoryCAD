"""MCP 的故事结构工具面。

**章是最底层，也是正文层**——场（Scene）与连线（ChapterEdge）已随数据层重构
一起删除。读取类的重活直接复用 ``ContextBuilder``，不在这里另写一份逻辑
（两份真相必然漂移）。

没有"重算字数"这类修复工具：字数实时算、顺序由实现归一化、不变式由结构保证。
"""
import uuid

from sqlalchemy import select

from app.agent.context import ContextBuilder
from app.mcp.server import mcp
from app.database import async_session
from app.mcp.auth import get_current_user_mcp, verify_project_ownership
from app.storycad.models import Act, Chapter, Setup
from app.storycad.order import order_by_sequence
from app.storycad.repository import with_chapter_content
from app.storycad.sequence import move_item
from app.utils import row_to_dict


@mcp.tool()
async def read_chapter(token: str, chapter_id: str) -> dict:
    """获取一章的规划与结果（本章要写什么 / 本章写成了什么 / 状态），不含正文。"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        chapter = (await db.execute(
            with_chapter_content(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
        )).scalar_one_or_none()
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")
        await verify_project_ownership(str(chapter.project_id), user["id"], db)
        data = row_to_dict(chapter)          # row_to_dict 按 mapper 跳过 deferred 列
        data["body_chars"] = len(chapter.content or "")
        data["written"] = bool((chapter.content or "").strip())
        return data


@mcp.tool()
async def read_chapter_content(token: str, chapter_id: str) -> dict:
    """获取一章的正文全文。"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        chapter = (await db.execute(
            with_chapter_content(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
        )).scalar_one_or_none()
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")
        await verify_project_ownership(str(chapter.project_id), user["id"], db)
        return {
            "chapter_id": str(chapter.id),
            "title": chapter.title or "",
            "content": chapter.content or "",
            "body_chars": len(chapter.content or ""),
        }


@mcp.tool()
async def read_acts(token: str, project_id: str) -> list[dict]:
    """获取全部幕：幕名、这一幕要干什么、幕序。"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        acts = (await db.execute(
            select(Act).where(Act.project_id == uuid.UUID(project_id))
            .order_by(*order_by_sequence(Act))
        )).scalars().all()
        return [row_to_dict(a) for a in acts]


@mcp.tool()
async def read_chapters(token: str, project_id: str, chapter_from: int, chapter_to: int) -> dict:
    """按全局章序号读一个连续范围：每章的要写什么 + 写成了什么 + 状态 + 是否已有正文。

    全局章号从第 1 幕第 1 章起跨幕连续编号。
    """
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        builder = ContextBuilder(db)
        return await builder.build_chapter_window(uuid.UUID(project_id), chapter_from, chapter_to)


@mcp.tool()
async def read_progress(token: str, project_id: str) -> dict:
    """读取进度（算出来的清单）：设计缺什么、正文断点在第几章、伏笔逾期、最近改动的章、下一步。"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        builder = ContextBuilder(db)
        return await builder.build_progress(uuid.UUID(project_id))


@mcp.tool()
async def list_setups(token: str, project_id: str, status: str = "") -> list[dict]:
    """列出伏笔：埋的是什么、埋在哪一章、打算在哪一章收、收没收。可按 status 过滤。"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        query = select(Setup).where(Setup.project_id == uuid.UUID(project_id))
        if status:
            query = query.where(Setup.status == status)
        setups = (await db.execute(query.order_by(*order_by_sequence(Setup)))).scalars().all()
        return [row_to_dict(s) for s in setups]


@mcp.tool()
async def create_chapter(
    token: str,
    project_id: str,
    act_id: str,
    title: str,
    goal: str = "",
    after_id: str = "",
) -> dict:
    """在指定幕下新建一章。goal 是「本章要写什么」。

    默认接在所属幕末尾；传 after_id 则插到那一章之后。位置永远用"插到谁之后"
    表达——不接受裸序号。
    """
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        pid = uuid.UUID(project_id)
        act = (await db.execute(
            select(Act).where(Act.id == uuid.UUID(act_id), Act.project_id == pid)
        )).scalar_one_or_none()
        if not act:
            raise ValueError(f"Act {act_id} not in project {project_id}")

        chapter = Chapter(
            project_id=pid, act_id=act.id, title=title, goal=goal,
            status="draft", sort_order=0,
        )
        db.add(chapter)
        await db.flush()
        await move_item(
            db, Chapter, pid, item_id=chapter.id,
            after_id=uuid.UUID(after_id) if after_id else None,
            position="end", container_column="act_id", container_id=act.id,
        )
        await db.commit()
        return {"chapter_id": str(chapter.id), "title": chapter.title,
                "sort_order": chapter.sort_order}


@mcp.tool()
async def update_chapter(
    token: str,
    chapter_id: str,
    title: str = "",
    goal: str = "",
    result: str = "",
    status_value: str = "",
) -> dict:
    """修改一章：章名 / 本章要写什么 / 本章写成了什么 / 状态（draft|revising|final）。

    一章写完（正文落库）之后必须把「本章写成了什么」写回——它是下一章唯一的起点。
    """
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        chapter = (await db.execute(
            select(Chapter).where(Chapter.id == uuid.UUID(chapter_id))
        )).scalar_one_or_none()
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")
        await verify_project_ownership(str(chapter.project_id), user["id"], db)

        if title:
            chapter.title = title
        if goal:
            chapter.goal = goal
        if result:
            chapter.result = result
        if status_value:
            if status_value not in ("draft", "revising", "final"):
                raise ValueError("status 只能是 draft / revising / final")
            chapter.status = status_value
        await db.commit()
        return row_to_dict(chapter)


@mcp.tool()
async def write_chapter(token: str, chapter_id: str, content: str) -> dict:
    """写入或整体替换一章正文（重写也用它）。会覆盖已有正文。"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        chapter = (await db.execute(
            select(Chapter).where(Chapter.id == uuid.UUID(chapter_id))
        )).scalar_one_or_none()
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")
        await verify_project_ownership(str(chapter.project_id), user["id"], db)
        chapter.content = content
        await db.commit()
        return {"chapter_id": str(chapter.id), "body_chars": len(content)}
