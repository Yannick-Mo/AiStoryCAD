import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.project.models import Project


class ProjectRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, title: str, description: str, owner_id: uuid.UUID) -> Project:
        project = Project(title=title, description=description, owner_id=owner_id)
        self.db.add(project)
        await self.db.commit()
        await self.db.refresh(project)
        return project

    async def get(self, project_id: uuid.UUID) -> Project | None:
        result = await self.db.execute(select(Project).where(Project.id == project_id))
        return result.scalar_one_or_none()

    async def list_projects(self, owner_id: uuid.UUID, page: int = 1, size: int = 20, search: str = "", status: str = ""):
        query = select(Project).where(Project.owner_id == owner_id)
        if search:
            query = query.where(Project.title.ilike(f"%{search}%"))
        if status:
            query = query.where(Project.status == status)
        query = query.order_by(Project.updated_at.desc()).offset((page - 1) * size).limit(size)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def update(self, project_id: uuid.UUID, **kwargs) -> bool:
        project = await self.get(project_id)
        if not project:
            return False
        for key, value in kwargs.items():
            setattr(project, key, value)
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return True

    async def delete(self, project_id: uuid.UUID) -> bool:
        """删除项目。

        不再需要手动清理 project_configs / project_versions——两张表已经
        删除；幕 / 章 / 人物 / 关系 / 伏笔 / 会话 / 知识片段都是
        ``ON DELETE CASCADE``，数据库自己会收干净。
        """
        project = await self.get(project_id)
        if not project:
            return False
        try:
            await self.db.delete(project)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return True
