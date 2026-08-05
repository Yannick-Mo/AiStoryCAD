"""Rhythm analysis API routes."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_current_user
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
    try:
        analyzer = RhythmAnalyzer(db)
        return await analyzer.analyze(project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"节奏分析失败: {str(e)}")
