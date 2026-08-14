import uuid
from typing import Optional
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.project.models import Project, ProjectVersion, ProjectConfig

_VERSION_RETENTION = 100  # newest versions kept per project


class ProjectRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, title: str, description: str, owner_id: uuid.UUID) -> Project:
        project = Project(title=title, description=description, owner_id=owner_id)
        self.db.add(project)
        await self.db.commit()
        await self.db.refresh(project)
        return project

    async def get(self, project_id: uuid.UUID) -> Optional[Project]:
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
        project = await self.get(project_id)
        if not project:
            return False
        try:
            await self.db.execute(
                ProjectConfig.__table__.delete().where(ProjectConfig.project_id == project_id)
            )
            await self.db.execute(
                ProjectVersion.__table__.delete().where(ProjectVersion.project_id == project_id)
            )
            await self.db.delete(project)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return True

    async def save_version(self, project_id: uuid.UUID, snapshot: dict) -> ProjectVersion:
        pv = await self.append_version(project_id, snapshot)
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        await self.db.refresh(pv)
        return pv

    async def append_version(self, project_id: uuid.UUID, snapshot: dict) -> ProjectVersion:
        """Insert the next version row WITHOUT committing (caller owns the tx).

        Used when the version write must be atomic with surrounding changes
        (e.g. the editor sync path). Prunes to ``_VERSION_RETENTION`` rows.
        """
        result = await self.db.execute(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project_id)
            .order_by(desc(ProjectVersion.version)).limit(1)
        )
        latest = result.scalar_one_or_none()
        version = (latest.version + 1) if latest else 1
        pv = ProjectVersion(project_id=project_id, version=version, snapshot=snapshot)
        self.db.add(pv)
        await self.db.flush()
        await self.db.execute(
            ProjectVersion.__table__.delete().where(
                ProjectVersion.project_id == project_id,
                ProjectVersion.id.in_(
                    select(ProjectVersion.id)
                    .where(ProjectVersion.project_id == project_id)
                    .order_by(desc(ProjectVersion.version))
                    .offset(_VERSION_RETENTION)
                ),
            )
        )
        return pv

    async def latest_version(self, project_id: uuid.UUID) -> Optional[ProjectVersion]:
        result = await self.db.execute(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project_id)
            .order_by(desc(ProjectVersion.version)).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_versions(self, project_id: uuid.UUID, limit: int = 50) -> list[ProjectVersion]:
        result = await self.db.execute(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project_id)
            .order_by(desc(ProjectVersion.version))
            .limit(limit)
        )
        return result.scalars().all()

    async def get_config(self, project_id: uuid.UUID) -> Optional[ProjectConfig]:
        result = await self.db.execute(
            select(ProjectConfig).where(ProjectConfig.project_id == project_id)
        )
        return result.scalar_one_or_none()

    async def upsert_config(self, project_id: uuid.UUID, **kwargs) -> ProjectConfig:
        config = await self.get_config(project_id)
        if config:
            for key, value in kwargs.items():
                setattr(config, key, value)
        else:
            config = ProjectConfig(project_id=project_id, **kwargs)
            self.db.add(config)
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        await self.db.refresh(config)
        return config
