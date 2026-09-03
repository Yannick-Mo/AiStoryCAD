import uuid
from sqlalchemy import select
from app.mcp.server import mcp
from app.database import async_session
from app.storycad.models import Character, CharacterRelation
from app.storycad.repository import AiStoryCADRepository
from app.utils import row_to_dict
from app.mcp.auth import get_current_user_mcp, verify_project_ownership


def _clamp_relation_value(value: int, name: str) -> int:
    """Validate and clamp relation metric to 0-100 range."""
    if not isinstance(value, int) or value < 0 or value > 100:
        raise ValueError(f"{name} must be between 0 and 100, got {value}")
    return value


@mcp.tool()
async def list_characters(token: str, project_id: str) -> dict:
    """列出项目中所有角色及其关系信息"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        repo = AiStoryCADRepository(db)
        characters = await repo.list_entities(Character, project_id)
        relations_result = await db.execute(
            select(CharacterRelation).where(CharacterRelation.project_id == project_id)
        )
        relations = [row_to_dict(r) for r in relations_result.scalars().all()]
        return {"characters": characters, "relations": relations}


@mcp.tool()
async def create_character(
    token: str,
    project_id: str,
    name: str,
    role: str = "supporting",
    personality: str = "",
    appearance: str = "",
    background: str = "",
    motivation: str = "",
) -> dict:
    """创建新角色"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        repo = AiStoryCADRepository(db)
        data = {
            "project_id": project_id,
            "name": name,
            "role": role,
            "personality": personality,
            "appearance": appearance,
            "background": background,
            "motivation": motivation,
        }
        created = await repo.create_entity(Character, data)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return created


@mcp.tool()
async def update_character(
    token: str,
    character_id: str,
    name: str | None = None,
    role: str | None = None,
    personality: str | None = None,
    appearance: str | None = None,
    background: str | None = None,
    motivation: str | None = None,
) -> dict:
    """更新角色信息"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        result = await db.execute(select(Character).where(Character.id == uuid.UUID(character_id)))
        character = result.scalar_one_or_none()
        if not character:
            raise ValueError(f"Character {character_id} not found")
        await verify_project_ownership(str(character.project_id), user["id"], db)
        repo = AiStoryCADRepository(db)
        data = {"id": character_id}
        for field in ("name", "role", "personality", "appearance", "background", "motivation"):
            val = locals()[field]
            if val is not None:
                data[field] = val
        updated = await repo.update_entity(Character, data)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return updated


@mcp.tool()
async def update_relation(
    token: str,
    project_id: str,
    character_id: str,
    target_id: str,
    rel_type: str = "关联",
    label: str = "",
    description: str = "",
    trust: int = 50,
    threat: int = 50,
    attraction: int = 50,
    relation_id: str | None = None,
) -> dict:
    """创建或更新角色关系"""
    _clamp_relation_value(trust, "trust")
    _clamp_relation_value(threat, "threat")
    _clamp_relation_value(attraction, "attraction")
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        repo = AiStoryCADRepository(db)
        if relation_id:
            # 安全：按 ID 直接更新前必须确认关系属于当前项目，防止跨租户改他人关系。
            rel_result = await db.execute(
                select(CharacterRelation).where(CharacterRelation.id == uuid.UUID(relation_id))
            )
            relation = rel_result.scalar_one_or_none()
            if relation is None or relation.project_id != uuid.UUID(project_id):
                raise ValueError("关系不属于该项目")
            data = {"id": relation_id}
            for field in ("rel_type", "label", "description", "trust", "threat", "attraction"):
                val = locals()[field]
                if val is not None:
                    data[field] = val
            updated = await repo.update_entity(CharacterRelation, data)
            if not updated:
                raise ValueError(f"Relation {relation_id} not found")
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            return updated
        else:
            from uuid import UUID
            # 安全：关系两端角色都必须属于当前项目，否则可把关系挂到他人角色上。
            pid = uuid.UUID(project_id)
            for char_id in (character_id, target_id):
                char_result = await db.execute(
                    select(Character).where(
                        Character.id == UUID(char_id),
                        Character.project_id == pid,
                    )
                )
                if char_result.scalar_one_or_none() is None:
                    raise ValueError("无效的角色 ID（不属于当前项目）")
            data = {
                "project_id": project_id,
                "character_id": str(UUID(character_id)),
                "target_id": str(UUID(target_id)),
                "rel_type": rel_type,
                "label": label,
                "description": description,
                "trust": trust,
                "threat": threat,
                "attraction": attraction,
            }
            created = await repo.create_entity(CharacterRelation, data)
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            return created
