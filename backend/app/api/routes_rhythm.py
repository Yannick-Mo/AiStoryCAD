"""Rhythm analysis API routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_current_user
from app.api.rate_limiter import rate_limiter
from app.agent.rhythm.analyzer import RhythmAnalyzer
from app.agent.tools.base import verify_project_owner as _verify_tool_owner

router = APIRouter(prefix="/api/rhythm", tags=["Rhythm"])


async def _verify_project_owner(db: AsyncSession, project_id, user: dict) -> None:
    try:
        await _verify_tool_owner(db, project_id, user["id"])
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized to access this project")


@router.get("/projects/{project_id}/analyze")
async def analyze_rhythm(
    project_id,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_project_owner(db, project_id, user)
    if not await rate_limiter.check(f"rhythm:{user['id']}", max_attempts=30, window=60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    try:
        analyzer = RhythmAnalyzer(db)
        return await analyzer.analyze(project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"节奏分析失败: {str(e)}")
