import uuid
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.project.service import ProjectService

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("")
async def create_project(payload: dict, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    service = ProjectService(db)
    return await service.create_project(
        payload.get("title", "Untitled"),
        payload.get("description", ""),
        uuid.UUID(current_user["id"])
    )


@router.get("")
async def list_projects(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    status: str = Query(""),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = ProjectService(db)
    return await service.list_projects(uuid.UUID(current_user["id"]), page, size, search, status)


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    service = ProjectService(db)
    result = await service.get_project(project_id, uuid.UUID(current_user["id"]))
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


# 作品层的可写字段（规格第 3 节总表）：书名 / 类型 / 一句话梗概 /
# 当前已完成骨架 / 当前全局设定 / 目标字数。description 与 status 是规格未表态的两个
# 历史字段，暂留。``ending`` 列保留在库里但不再对外可写。
ALLOWED_PROJECT_FIELDS = {
    "title", "genre", "logline", "spine", "global_settings", "total_words",
    "description", "status",
}


@router.patch("/{project_id}")
async def update_project(project_id: uuid.UUID, payload: dict, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    filtered = {k: v for k, v in payload.items() if k in ALLOWED_PROJECT_FIELDS}
    service = ProjectService(db)
    ok = await service.update_project(project_id, uuid.UUID(current_user["id"]), **filtered)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True}


@router.delete("/{project_id}")
async def delete_project(project_id: uuid.UUID, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    service = ProjectService(db)
    ok = await service.delete_project(project_id, uuid.UUID(current_user["id"]))
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True}
