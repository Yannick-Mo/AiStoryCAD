import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_current_user
from app.project.service import ProjectService
from app.storycad.repository import AiStoryCADRepository, ENTITY_FK_MAP
from app.storycad.entity_map import ENTITY_MAP
from app.storycad.models import Act, Chapter
from app.storycad.sequence import move_item, ordered_chapter_ids

router = APIRouter(prefix="/api/projects/{project_id}", tags=["storycad"])


async def _check_project_owner(project_id: uuid.UUID, current_user: dict, db: AsyncSession):
    svc = ProjectService(db)
    project = await svc.get_project(project_id, uuid.UUID(current_user["id"]))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")


async def _validate_fk_targets(db: AsyncSession, model_class: type, payload: dict, project_id_str: str):
    # 安全：校验 payload 中的每个外键都指向当前项目下的行，防止跨租户写入。
    fk_map = ENTITY_FK_MAP.get(model_class)
    if not fk_map:
        return
    pid = uuid.UUID(project_id_str)
    for column_name, target_model in fk_map.items():
        value = payload.get(column_name)
        if value is None or value == "":
            continue
        try:
            target_id = uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(status_code=400, detail=f"无效的 {column_name}（不是有效的 ID）") from exc
        result = await db.execute(
            select(target_model.id).where(
                target_model.id == target_id,
                target_model.project_id == pid,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail=f"无效的 {column_name}（不属于当前项目）")


async def _get_repo(db: AsyncSession) -> AiStoryCADRepository:
    return AiStoryCADRepository(db)


# ============================================================
# Editor data: full load + incremental sync
# ============================================================

@router.get("/editor-data")
async def get_editor_data(
    project_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    repo = await _get_repo(db)
    return await repo.get_editor_data(project_id)


@router.post("/editor-data/sync")
async def sync_editor_data(
    project_id: uuid.UUID,
    payload: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """编辑器增量同步。

    不再返回 ``version``：那是 ``project_versions`` 的编辑器同步游标，
    既不是版本也不能回滚，已随该表一起删除。顺序的归一化由服务端在
    同一次调用里完成（规格第 5 节）。
    """
    await _check_project_owner(project_id, current_user, db)
    repo = await _get_repo(db)
    return {"ok": True, **(await repo.sync_editor_data(project_id, payload))}


# ============================================================
# Chapter content (deferred column, loaded on demand)
# ============================================================

@router.get("/chapters/content")
async def get_all_chapter_contents(
    project_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    repo = await _get_repo(db)
    return {"contents": await repo.get_all_chapter_contents(project_id)}


@router.get("/chapters/{chapter_id}/content")
async def get_chapter_content(
    project_id: uuid.UUID,
    chapter_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    repo = await _get_repo(db)
    content = await repo.get_chapter_content(chapter_id, project_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"chapter_id": str(chapter_id), "content": content}


@router.put("/chapters/{chapter_id}/content")
async def save_chapter_content(
    project_id: uuid.UUID,
    chapter_id: uuid.UUID,
    payload: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    repo = await _get_repo(db)
    content = payload.get("content", "")
    ok = await repo.save_chapter_content(chapter_id, project_id, content)
    if ok is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    # 字数不再落库（派生值不存），这里只是回给编辑器一个即时反馈。
    from app.agent.utils import count_words
    return {"ok": True, "word_count": count_words(content)}


# ============================================================
# Generic entity CRUD (acts, chapters, characters, relations, setups)
# ============================================================

# 哪些实体可以"移动"，以及它的顺序容器是什么。
# 幕是项目级的（没有父容器），章挂在幕下。
_MOVE_CONTAINER: dict[type, tuple[str | None, type | None]] = {
    Chapter: ("act_id", Act),
    Act: (None, None),
}


@router.post("/{entity_type}/{entity_id}/move")
async def move_entity(
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    payload: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把一项插到另一项之后（或插到最前 / 最后）。

    顺序规范（规格第 5 节）：移动**只用"插到谁之后"表达**，这个端点收的是
    ``after_id`` / ``position``，**不收裸序号**——同层序号由实现归一化成连续
    整数，客户端不需要也不应该自己算。章还能顺便换幕（传 ``act_id``）。
    """
    await _check_project_owner(project_id, current_user, db)
    model_class = ENTITY_MAP.get(entity_type)
    if model_class is None or model_class not in _MOVE_CONTAINER:
        raise HTTPException(status_code=400, detail=f"实体类型 {entity_type} 不支持移动")

    container_column, container_model = _MOVE_CONTAINER[model_class]
    entity = (await db.execute(
        select(model_class).where(model_class.id == entity_id)
    )).scalar_one_or_none()
    if entity is None or entity.project_id != project_id:
        raise HTTPException(status_code=404, detail="Entity not found")

    container_id = getattr(entity, container_column) if container_column else None
    act_raw = payload.get("act_id")
    if container_column and act_raw:
        try:
            container_id = uuid.UUID(str(act_raw))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="无效的 act_id（不是有效的 ID）") from exc
        target = (await db.execute(
            select(container_model.id).where(
                container_model.id == container_id,
                container_model.project_id == project_id,
            )
        )).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=400, detail="无效的 act_id（不属于当前项目）")

    after_id = None
    after_raw = payload.get("after_id")
    if after_raw:
        try:
            after_id = uuid.UUID(str(after_raw))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="无效的 after_id（不是有效的 ID）") from exc

    position = payload.get("position") or "end"
    if position not in ("start", "end"):
        raise HTTPException(status_code=400, detail="position 只能是 start 或 end")

    try:
        await move_item(
            db, model_class, project_id,
            item_id=entity_id, after_id=after_id, position=position,
            container_column=container_column, container_id=container_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()

    global_order = None
    if model_class is Chapter:
        ordered = await ordered_chapter_ids(db, project_id)
        global_order = (ordered.index(entity.id) + 1) if entity.id in ordered else None
    return {
        "ok": True,
        "id": str(entity.id),
        "act_id": str(container_id) if container_id else None,
        "sort_order": entity.sort_order,
        "global_order": global_order,
    }


@router.get("/{entity_type}")
async def list_entities(
    project_id: uuid.UUID,
    entity_type: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    model_class = ENTITY_MAP.get(entity_type)
    if not model_class:
        raise HTTPException(status_code=400, detail=f"Unknown entity type: {entity_type}")
    repo = await _get_repo(db)
    return await repo.list_entities(model_class, project_id)


@router.post("/{entity_type}")
async def create_entity(
    project_id: uuid.UUID,
    entity_type: str,
    payload: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    model_class = ENTITY_MAP.get(entity_type)
    if not model_class:
        raise HTTPException(status_code=400, detail=f"Unknown entity type: {entity_type}")
    payload["project_id"] = str(project_id)
    # 安全：写入前校验所有外键目标均属于当前项目。
    await _validate_fk_targets(db, model_class, payload, str(project_id))
    repo = await _get_repo(db)
    # 位置由服务端定（接在容器末尾）：客户端传的裸序号会被丢弃。
    await repo.assign_initial_sort_order(model_class, project_id, payload)
    result = await repo.create_entity(model_class, payload)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return result


@router.get("/{entity_type}/{entity_id}")
async def get_entity(
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    model_class = ENTITY_MAP.get(entity_type)
    if not model_class:
        raise HTTPException(status_code=400, detail=f"Unknown entity type: {entity_type}")
    repo = await _get_repo(db)
    result = await repo.get_entity(model_class, entity_id)
    if not result:
        raise HTTPException(status_code=404, detail="Entity not found")
    if result.get("project_id") != str(project_id):
        raise HTTPException(status_code=404, detail="Entity not found")
    return result


@router.put("/{entity_type}/{entity_id}")
async def update_entity(
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    payload: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    model_class = ENTITY_MAP.get(entity_type)
    if not model_class:
        raise HTTPException(status_code=400, detail=f"Unknown entity type: {entity_type}")
    repo = await _get_repo(db)
    entity = await repo.get_entity(model_class, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if entity.get("project_id") != str(project_id):
        raise HTTPException(status_code=404, detail="Entity not found")
    payload["id"] = str(entity_id)
    # 安全：更新外键列时同样校验目标属于当前项目。
    await _validate_fk_targets(db, model_class, payload, str(project_id))
    result = await repo.update_entity(model_class, payload)
    if not result:
        raise HTTPException(status_code=404, detail="Entity not found")
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return result


@router.delete("/{entity_type}/{entity_id}")
async def delete_entity(
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    model_class = ENTITY_MAP.get(entity_type)
    if not model_class:
        raise HTTPException(status_code=400, detail=f"Unknown entity type: {entity_type}")
    repo = await _get_repo(db)
    entity = await repo.get_entity(model_class, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if entity.get("project_id") != str(project_id):
        raise HTTPException(status_code=404, detail="Entity not found")
    ok = await repo.delete_entity(model_class, entity_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Entity not found")
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"ok": True}
