import uuid
from sqlalchemy import select
from app.mcp.server import mcp
from app.database import async_session
from app.project.repository import ProjectRepository
from app.storycad.models import Act, Chapter
from app.storycad.order import order_by_sequence
from app.utils import row_to_dict
from app.mcp.auth import get_current_user_mcp, verify_project_ownership


@mcp.tool()
async def read_project(token: str, project_id: str) -> dict:
    """加载完整项目上下文，包括标题、体裁、描述和配置"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        repo = ProjectRepository(db)
        project = await repo.get(uuid.UUID(project_id))
        if not project:
            raise ValueError(f"Project {project_id} not found")
        config = await repo.get_config(uuid.UUID(project_id))
        data = row_to_dict(project)
        if config:
            data["config"] = row_to_dict(config)
        return data


@mcp.tool()
async def update_project(
    token: str,
    project_id: str,
    title: str | None = None,
    description: str | None = None,
    genre: str | None = None,
    status: str | None = None,
    global_settings: str | None = None,
) -> dict:
    """更新项目元数据（标题、描述、体裁、状态、全局设定）"""
    kwargs = {}
    if title is not None:
        kwargs["title"] = title
    if description is not None:
        kwargs["description"] = description
    if genre is not None:
        kwargs["genre"] = genre
    if status is not None:
        kwargs["status"] = status
    if global_settings is not None:
        kwargs["global_settings"] = global_settings
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        repo = ProjectRepository(db)
        ok = await repo.update(uuid.UUID(project_id), **kwargs)
        if not ok:
            raise ValueError(f"Project {project_id} not found")
        project = await repo.get(uuid.UUID(project_id))
        return row_to_dict(project)


@mcp.tool()
async def list_chapters(token: str, project_id: str) -> list[dict]:
    """列出项目中所有章节及其排序"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        result = await db.execute(
            select(Chapter)
            .outerjoin(Act, Act.id == Chapter.act_id)
            .where(Chapter.project_id == uuid.UUID(project_id))
            .order_by(Act.sort_order.asc(), *order_by_sequence(Chapter))
        )
        return [row_to_dict(c) for c in result.scalars().all()]
