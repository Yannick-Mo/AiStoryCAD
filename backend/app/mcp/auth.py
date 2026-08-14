import uuid
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.user.repository import UserRepository
from app.project.models import Project

_JWT_AUDIENCE = "storycad-api"


async def get_current_user_mcp(token: str, db: AsyncSession) -> dict:
    """Validate JWT token and return user info. Used by MCP tools."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            audience=_JWT_AUDIENCE,
            options={"verify_exp": True, "require": ["sub", "exp", "iss", "aud", "jti"]},
        )
        user_id = uuid.UUID(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise ValueError("Invalid authentication token")
    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if not user:
        raise ValueError("User not found")
    return {"id": str(user.id), "username": user.username, "email": user.email}


async def verify_project_ownership(project_id: str, user_id: str, db: AsyncSession) -> None:
    """Verify that the user owns the project. Raises ValueError if not."""
    result = await db.execute(
        select(Project).where(
            Project.id == uuid.UUID(project_id),
            Project.owner_id == uuid.UUID(user_id),
        )
    )
    if not result.scalar_one_or_none():
        raise ValueError("Project not found or access denied")
