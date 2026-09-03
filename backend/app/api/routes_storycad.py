import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_current_user
from app.project.service import ProjectService
from app.storycad.repository import AiStoryCADRepository
from app.storycad.models import (
    Scene, Chapter, Act, ChapterEdge, Character, CharacterRelation,
    Theme, ThemeChapter, ChapterRhythm,
)
from app.storycad.entity_map import ENTITY_MAP

router = APIRouter(prefix="/api/projects/{project_id}", tags=["storycad"])


# 外键列 → 目标模型映射：写入时必须校验目标行属于当前项目，
# 否则攻击者可把场景/边/关系挂到他人项目下的章节/角色上。
_ENTITY_FK_MAP = {
    Chapter: {"act_id": Act},
    Scene: {"chapter_id": Chapter},
    ChapterEdge: {"source_id": Chapter, "target_id": Chapter},
    CharacterRelation: {"character_id": Character, "target_id": Character},
    ThemeChapter: {"theme_id": Theme, "chapter_id": Chapter},
    ChapterRhythm: {"chapter_id": Chapter},
}


async def _check_project_owner(project_id: uuid.UUID, current_user: dict, db: AsyncSession):
    svc = ProjectService(db)
    project = await svc.get_project(project_id, uuid.UUID(current_user["id"]))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")


async def _validate_fk_targets(db: AsyncSession, model_class: type, payload: dict, project_id_str: str):
    # 安全：校验 payload 中的每个外键都指向当前项目下的行，防止跨租户写入。
    fk_map = _ENTITY_FK_MAP.get(model_class)
    if not fk_map:
        return
    pid = uuid.UUID(project_id_str)
    for column_name, target_model in fk_map.items():
        value = payload.get(column_name)
        if value is None or value == "":
            continue
        try:
            target_id = uuid.UUID(str(value))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=400, detail=f"无效的 {column_name}（不是有效的 ID）")
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
    await _check_project_owner(project_id, current_user, db)
    repo = await _get_repo(db)
    version = await repo.sync_editor_data(project_id, payload)
    return {"ok": True, "version": version}


# ============================================================
# Scene content (lazy-loaded large text)
# ============================================================

@router.get("/scenes/content")
async def get_all_scene_contents(
    project_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    repo = await _get_repo(db)
    contents = await repo.get_all_scene_contents(project_id)
    return {"contents": contents}


@router.get("/scenes/{scene_id}/content")
async def get_scene_content(
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    repo = await _get_repo(db)
    content = await repo.get_scene_content(scene_id, project_id)
    return {"scene_id": str(scene_id), "content": content or ""}


@router.put("/scenes/{scene_id}/content")
async def save_scene_content(
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
    payload: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_project_owner(project_id, current_user, db)
    repo = await _get_repo(db)
    content = payload.get("content", "")
    from app.agent.utils import count_words
    word_count = count_words(content)
    ok = await repo.save_scene_content(scene_id, project_id, content)
    if ok is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    from sqlalchemy import select, func
    try:
        result = await db.execute(select(Scene).where(Scene.id == scene_id))
        scene = result.scalar_one_or_none()
        if scene:
            scene.word_count = word_count
            ch_total = await db.execute(
                select(func.coalesce(func.sum(Scene.word_count), 0))
                .where(Scene.chapter_id == scene.chapter_id)
            )
            ch_word_count = ch_total.scalar() or 0
            await db.execute(
                Chapter.__table__.update().where(Chapter.id == scene.chapter_id)
                .values(total_words=ch_word_count)
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"ok": True, "word_count": word_count}


# ============================================================
# Generic entity CRUD (acts, chapters, edges, characters, etc.)
# ============================================================

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
