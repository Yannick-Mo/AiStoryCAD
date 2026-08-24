"""MCP auth shim for the single-user local tool.

The MCP tools keep calling ``get_current_user_mcp`` / ``verify_project_ownership``
for signature compatibility; in local mode both are no-ops that always pass.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from app.settings.service import local_user


async def get_current_user_mcp(token: str, db: AsyncSession) -> dict:
    """Local tool: token is ignored, the fixed local identity is returned."""
    return local_user()


async def verify_project_ownership(project_id: str, user_id: str, db: AsyncSession) -> None:
    """Local tool: no ownership checks; always passes."""
    return None
