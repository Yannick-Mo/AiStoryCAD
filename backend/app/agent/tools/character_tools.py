from __future__ import annotations

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.storycad.models import Character, CharacterRelation
from app.storycad.repository import AiStoryCADRepository
from app.utils import row_to_dict


class ListCharactersTool(BaseTool):
    meta = ToolMeta(
        name="list_characters",
        description="列出项目中所有角色及其详细信息，同时返回角色关系",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            repo = AiStoryCADRepository(db)
            characters = await repo.list_entities(Character, pid)
            relations_result = await db.execute(
                select(CharacterRelation).where(CharacterRelation.project_id == pid)
            )
            relations = [row_to_dict(r) for r in relations_result.scalars().all()]
            return ToolResult(success=True, data={"characters": characters, "relations": relations})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ReadCharacterTool(BaseTool):
    meta = ToolMeta(
        name="read_character",
        description="获取单个角色的完整档案（名称、类型、性格、外貌、背景、动机）。character_id 来自 list_characters 或 read_project_overview",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "角色ID，来自 list_characters 或 read_project_overview 返回结果"},
            },
            "required": ["character_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            char_id_val = self._require_param(kwargs, "character_id")
            if char_id_val is None:
                return self._missing_param("character_id")
            char_id = uuid.UUID(char_id_val)
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            # 安全：按项目过滤角色查询，防止跨项目按 UUID 读取（IDOR）
            result = await db.execute(
                select(Character).where(Character.id == char_id, Character.project_id == pid)
            )
            char = result.scalar_one_or_none()
            if not char:
                return self._not_found("Character")
            return ToolResult(success=True, data=row_to_dict(char))
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class CreateCharacterTool(BaseTool):
    meta = ToolMeta(
        name="create_character",
        description="创建新角色，需提供名称，可选角色类型、性格、外貌、背景、动机",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "角色名称"},
                "role": {"type": "string", "description": "角色类型：protagonist（主角）/supporting（配角）/antagonist（反派）"},
                "personality": {"type": "string", "description": "性格描述"},
                "appearance": {"type": "string", "description": "外貌描述"},
                "background": {"type": "string", "description": "背景故事"},
                "motivation": {"type": "string", "description": "动机"},
            },
            "required": ["name"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_val = self._require_param(kwargs, "project_id")
            if pid_val is None:
                return self._missing_param("project_id")
            name = self._require_param(kwargs, "name")
            if name is None:
                return self._missing_param("name")
            pid = uuid.UUID(pid_val)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            repo = AiStoryCADRepository(db)
            existing = await db.execute(
                select(Character).where(
                    Character.project_id == pid,
                    Character.name == name,
                )
            )
            if existing.scalar_one_or_none():
                return self._already_exists("角色", name)
            data = {
                "project_id": str(pid),
                "name": name,
                "role": kwargs.get("role", "supporting"),
                "personality": kwargs.get("personality", ""),
                "appearance": kwargs.get("appearance", ""),
                "background": kwargs.get("background", ""),
                "motivation": kwargs.get("motivation", ""),
            }
            created = await repo.create_entity(Character, data)
            await db.commit()
            return ToolResult(success=True, data=created)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class DeleteCharacterTool(BaseTool):
    meta = ToolMeta(
        name="delete_character",
        description="删除指定角色（同时删除该角色的所有关系连线）。character_id 来自 list_characters",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "角色ID，来自 list_characters 返回结果"},
            },
            "required": ["character_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            char_id_val = self._require_param(kwargs, "character_id")
            if char_id_val is None:
                return self._missing_param("character_id")
            char_id = uuid.UUID(char_id_val)
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            char = await db.get(Character, char_id)
            if not char:
                return self._not_found("Character")
            if char.project_id != pid:
                return self._permission_denied("角色")
            name = char.name
            # Delete all relations involving this character
            rels = (await db.execute(
                select(CharacterRelation).where(
                    (CharacterRelation.character_id == char_id)
                    | (CharacterRelation.target_id == char_id)
                )
            )).scalars().all()
            deleted_relation_ids = [str(r.id) for r in rels]
            for rel in rels:
                await db.delete(rel)
            await db.delete(char)
            await db.commit()
            return ToolResult(success=True, data={
                "deleted": name,
                "character_id": str(char_id),
                "deleted_relation_ids": deleted_relation_ids,
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class DeleteRelationTool(BaseTool):
    meta = ToolMeta(
        name="delete_relation",
        description="删除角色关系连线。relation_id 来自 list_relations",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "relation_id": {"type": "string", "description": "关系ID，来自 list_relations 返回结果"},
            },
            "required": ["relation_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            rel_id = uuid.UUID(kwargs["relation_id"])
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            rel = await db.get(CharacterRelation, rel_id)
            if not rel:
                return self._not_found("CharacterRelation")
            if rel.project_id != pid:
                return self._permission_denied("角色关系")
            await db.delete(rel)
            await db.commit()
            return ToolResult(success=True, data={"deleted": rel.label or rel.rel_type, "relation_id": str(rel_id)})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class UpdateCharacterTool(BaseTool):
    meta = ToolMeta(
        name="update_character",
        description="更新角色信息，需提供角色ID，可选更新名称、类型、性格、外貌、背景、动机。character_id 来自 list_characters",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "角色ID，来自 list_characters 返回结果"},
                "name": {"type": "string", "description": "角色名称"},
                "role": {"type": "string", "description": "角色类型：protagonist（主角）/supporting（配角）/antagonist（反派）"},
                "personality": {"type": "string", "description": "性格描述"},
                "appearance": {"type": "string", "description": "外貌描述"},
                "background": {"type": "string", "description": "背景故事"},
                "motivation": {"type": "string", "description": "动机"},
            },
            "required": ["character_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            char_id_val = self._require_param(kwargs, "character_id")
            if char_id_val is None:
                return self._missing_param("character_id")
            char_id = uuid.UUID(char_id_val)
            result = await db.execute(select(Character).where(Character.id == char_id))
            char_obj = result.scalar_one_or_none()
            if not char_obj:
                return self._not_found("Character")
            await verify_project_owner(db, char_obj.project_id, kwargs.get("user_id"))
            repo = AiStoryCADRepository(db)
            data = {"id": str(char_id)}
            for field in ("name", "role", "personality", "appearance", "background", "motivation"):
                if field in kwargs:
                    data[field] = kwargs[field]
            updated = await repo.update_entity(Character, data)
            if not updated:
                return self._not_found("Character")
            await db.commit()
            return ToolResult(success=True, data=updated)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class CreateRelationTool(BaseTool):
    meta = ToolMeta(
        name="create_relation",
        description="在两个角色之间创建关系连线，需提供源角色ID和目标角色ID",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "源角色ID，来自 list_characters 返回结果"},
                "target_id": {"type": "string", "description": "目标角色ID，来自 list_characters 返回结果"},
                "rel_type": {"type": "string", "description": "关系类型"},
                "label": {"type": "string", "description": "关系标签"},
                "description": {"type": "string", "description": "关系描述"},
                "trust": {"type": "integer", "description": "信任度（0-100）", "minimum": 0, "maximum": 100},
                "threat": {"type": "integer", "description": "威胁度（0-100）", "minimum": 0, "maximum": 100},
                "attraction": {"type": "integer", "description": "吸引力（0-100）", "minimum": 0, "maximum": 100},
            },
            "required": ["character_id", "target_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            char_id = uuid.UUID(kwargs["character_id"])
            tgt_id = uuid.UUID(kwargs["target_id"])
            char = await db.get(Character, char_id)
            if not char or char.project_id != pid:
                return self._not_found("Character in project")
            tgt = await db.get(Character, tgt_id)
            if not tgt or tgt.project_id != pid:
                return self._not_found("Character in project")
            repo = AiStoryCADRepository(db)
            data = {
                "project_id": str(pid),
                "character_id": str(char_id),
                "target_id": str(tgt_id),
                "rel_type": kwargs.get("rel_type", "关联"),
                "label": kwargs.get("label", ""),
                "description": kwargs.get("description", ""),
                "trust": kwargs.get("trust", 50),
                "threat": kwargs.get("threat", 50),
                "attraction": kwargs.get("attraction", 50),
            }
            created = await repo.create_entity(CharacterRelation, data)
            await db.commit()
            return ToolResult(success=True, data=created)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class UpdateRelationTool(BaseTool):
    meta = ToolMeta(
        name="update_relation",
        description="更新已有角色关系的类型、标签、描述或三维数值（信任/威胁/吸引力）。relation_id 来自 list_relations",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "relation_id": {"type": "string", "description": "关系ID，来自 list_relations 返回结果"},
                "rel_type": {"type": "string", "description": "关系类型"},
                "label": {"type": "string", "description": "关系标签"},
                "description": {"type": "string", "description": "关系描述"},
                "trust": {"type": "integer", "description": "信任度（0-100）", "minimum": 0, "maximum": 100},
                "threat": {"type": "integer", "description": "威胁度（0-100）", "minimum": 0, "maximum": 100},
                "attraction": {"type": "integer", "description": "吸引力（0-100）", "minimum": 0, "maximum": 100},
            },
            "required": ["relation_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            relation_id = uuid.UUID(kwargs["relation_id"])
            rel = await db.get(CharacterRelation, relation_id)
            if not rel:
                return self._not_found("CharacterRelation")
            if rel.project_id != pid:
                return self._permission_denied("角色关系")
            repo = AiStoryCADRepository(db)
            data = {"id": str(relation_id)}
            for field in ("rel_type", "label", "description", "trust", "threat", "attraction"):
                if field in kwargs:
                    data[field] = kwargs[field]
            updated = await repo.update_entity(CharacterRelation, data)
            if not updated:
                return self._not_found("CharacterRelation")
            await db.commit()
            return ToolResult(success=True, data=updated)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))
