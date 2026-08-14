import uuid
from typing import Optional
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.user.models import User
from app.project.models import Project, ProjectConfig, ProjectVersion
from app.agent.memory.models import Conversation, ConversationMessage
from app.knowledge.models import KnowledgeChunk


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, username: str, email: str, password_hash: str) -> User:
        user = User(username=username, email=email, password_hash=password_hash)
        self.db.add(user)
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        await self.db.refresh(user)
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def update(self, user_id: uuid.UUID, **kwargs) -> bool:
        user = await self.get_by_id(user_id)
        if not user:
            return False
        for key, value in kwargs.items():
            setattr(user, key, value)
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return True

    async def delete(self, user_id: uuid.UUID) -> bool:
        user = await self.db.get(User, user_id)
        if not user:
            return False
        try:
            # Child rows first (FK-safe), then projects, then the user.
            await self.db.execute(
                delete(ConversationMessage).where(
                    ConversationMessage.conversation_id.in_(
                        select(Conversation.id).where(Conversation.user_id == user_id)
                    )
                )
            )
            await self.db.execute(
                delete(Conversation).where(Conversation.user_id == user_id)
            )
            await self.db.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.user_id == user_id)
            )
            result = await self.db.execute(
                select(Project).where(Project.owner_id == user_id)
            )
            projects = result.scalars().all()
            for project in projects:
                await self.db.execute(
                    ProjectConfig.__table__.delete().where(ProjectConfig.project_id == project.id)
                )
                await self.db.execute(
                    ProjectVersion.__table__.delete().where(ProjectVersion.project_id == project.id)
                )
                await self.db.delete(project)
            await self.db.delete(user)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return True
