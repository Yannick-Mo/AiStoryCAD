"""人物与角色关系写入工具——规格第 9 节「关系」组。

人物回答三件事：**他是谁（静态：性格/外貌/背景）· 他想要什么（当下：motivation）·
他从哪到哪（弧线：arc）**。

"他从哪到哪"是**设计意图**（起点→终点），不是随写作变化的记录；
"他此刻在哪、手里有什么"属于「本章写成了什么」——那是历史，不是人物卡。

角色关系是一条条**记录**，不是一张图。三个强度值（信任/威胁/吸引）已删除：
它们是伪精度，而且关系强度随剧情变化属于动态——静态记录里放动态值必然过时。
"""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolMeta, ToolResult, verify_project_owner
from app.storycad.models import Character, CharacterRelation
from app.storycad.order import order_by_sequence

_ROLES = ("main", "supporting", "minor")

_CHARACTER_FIELDS = {
    "name": {"type": "string", "description": "姓名"},
    "role": {
        "type": "string",
        "description": "定位：main（主角）/ supporting（配角）/ minor（次要）",
        "enum": list(_ROLES),
    },
    "personality": {"type": "string", "description": "性格——他是谁（静态）"},
    "appearance": {"type": "string", "description": "外貌——他长什么样"},
    "background": {"type": "string", "description": "背景——他之前经历过什么"},
    "motivation": {"type": "string", "description": "他想要什么——他现在的欲望"},
    "arc": {"type": "string", "description": "他从哪到哪——开始时他是什么样、最后变成什么样（这是设计意图，不是随写作变化的记录）"},
}


async def _owned_character(db: AsyncSession, project_id: uuid.UUID, raw: str) -> Character | None:
    try:
        char_id = uuid.UUID(raw)
    except (ValueError, TypeError):
        return None
    char = (await db.execute(select(Character).where(Character.id == char_id))).scalar_one_or_none()
    if char is None or char.project_id != project_id:
        return None
    return char


class CreateCharacterTool(BaseTool):
    meta = ToolMeta(
        name="create_character",
        description="建一个人物：姓名 + 定位，以及性格 / 外貌 / 背景 / **他想要什么** / **他从哪到哪**。"
                    "这两个尽量当场写清",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": dict(_CHARACTER_FIELDS),
            "required": ["name"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            name = self._require_param(kwargs, "name")
            if name is None:
                return self._missing_param("name")
            role = kwargs.get("role")
            if role is not None and role not in _ROLES:
                return ToolResult(success=False, error=f"无效的 role '{role}'，有效值为：{', '.join(_ROLES)}")

            max_order = 0
            existing = (await db.execute(
                select(Character).where(Character.project_id == pid).order_by(*order_by_sequence(Character))
            )).scalars().all()
            if existing:
                max_order = max(c.sort_order or 0 for c in existing)
            if any((c.name or "") == name for c in existing):
                return self._already_exists("人物", name)

            char = Character(
                project_id=pid,
                name=name,
                role=role or "supporting",
                sort_order=max_order + 1,
                **{field: (kwargs.get(field) or "") for field in
                   ("personality", "appearance", "background", "motivation", "arc")},
            )
            db.add(char)
            await db.commit()
            return ToolResult(success=True, data={
                "character_id": str(char.id),
                "name": char.name,
                "role": char.role,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class UpdateCharacterTool(BaseTool):
    meta = ToolMeta(
        name="update_character",
        description="改人物档案：姓名 / 定位 / 性格 / 外貌 / 背景 / **他想要什么** / **他从哪到哪**。"
                    "只传要改的字段；写作推进到某人处境变了时，改的是「他想要什么」",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {"character_id": {"type": "string", "description": "人物ID，来自 list_characters"},
                           **_CHARACTER_FIELDS},
            "required": ["character_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            cid_raw = self._require_param(kwargs, "character_id")
            if cid_raw is None:
                return self._missing_param("character_id")
            char = await _owned_character(db, uuid.UUID(kwargs["project_id"]), cid_raw)
            if char is None:
                return self._not_found("Character in project")
            await verify_project_owner(db, char.project_id, kwargs.get("user_id"))

            if "role" in kwargs and kwargs["role"] is not None:
                if kwargs["role"] not in _ROLES:
                    return ToolResult(success=False,
                                      error=f"无效的 role '{kwargs['role']}'，有效值为：{', '.join(_ROLES)}")
                char.role = kwargs["role"]
            for field in ("name", "personality", "appearance", "background", "motivation", "arc"):
                if field in kwargs and kwargs[field] is not None:
                    setattr(char, field, kwargs[field])
            await db.commit()
            return ToolResult(success=True, data={
                "character_id": str(char.id),
                "name": char.name or "",
                "role": char.role or "",
                "has_motivation": bool((char.motivation or "").strip()),
                "has_arc": bool((char.arc or "").strip()),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class DeleteCharacterTool(BaseTool):
    meta = ToolMeta(
        name="delete_character",
        description="删除一个人物——同时删掉他身上所有的关系记录。只是想让他退场（不再登场）把定位改成 minor 即可",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "要删除的人物ID，来自 list_characters"},
            },
            "required": ["character_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            cid_raw = self._require_param(kwargs, "character_id")
            if cid_raw is None:
                return self._missing_param("character_id")
            char = await _owned_character(db, uuid.UUID(kwargs["project_id"]), cid_raw)
            if char is None:
                return self._not_found("Character in project")
            await verify_project_owner(db, char.project_id, kwargs.get("user_id"))
            char_id, name, project_id = char.id, char.name, char.project_id
            # 关系行会被外键级联删掉，所以必须把它们的 id 也交出去——不交，
            # ID 寄存表就会一直留着已经不存在的关系 ID。
            cascaded_relation_ids = [
                str(rid) for (rid,) in (await db.execute(
                    select(CharacterRelation.id).where(
                        CharacterRelation.project_id == project_id,
                        or_(CharacterRelation.character_id == char_id,
                            CharacterRelation.target_id == char_id),
                    )
                )).all()
            ]
            await db.delete(char)
            await db.commit()
            return ToolResult(success=True, data={
                "deleted_character_id": str(char_id),
                "name": name or "",
                "deleted_relation_ids": cascaded_relation_ids,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class CreateRelationTool(BaseTool):
    meta = ToolMeta(
        name="create_relation",
        description="记一条角色关系：谁（character_id）和谁（target_id）是什么关系（rel_type）"
                    "、更具体的说法（label）、以及来龙去脉（description）",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "甲的ID，来自 list_characters"},
                "target_id": {"type": "string", "description": "乙的ID，来自 list_characters"},
                "rel_type": {"type": "string", "description": "关系类型，例如：亲属 / 同事 / 对手"},
                "label": {"type": "string", "description": "更具体的说法，例如「父亲之死的知情人」"},
                "description": {"type": "string", "description": "这段关系的来龙去脉"},
            },
            "required": ["character_id", "target_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            src_raw = self._require_param(kwargs, "character_id")
            tgt_raw = self._require_param(kwargs, "target_id")
            if src_raw is None:
                return self._missing_param("character_id")
            if tgt_raw is None:
                return self._missing_param("target_id")
            src = await _owned_character(db, pid, src_raw)
            if src is None:
                return self._not_found("Character in project")
            tgt = await _owned_character(db, pid, tgt_raw)
            if tgt is None:
                return self._not_found("Character in project")
            if src.id == tgt.id:
                return ToolResult(
                    success=False,
                    error="关系不能连到同一个人自己（character_id 与 target_id 相同）",
                    correction_hint="这条记录是两个**不同**的人之间是什么关系；若只想记他的性格，改人物档案即可",
                )

            existing = (await db.execute(
                select(CharacterRelation).where(
                    CharacterRelation.project_id == pid,
                    CharacterRelation.character_id == src.id,
                    CharacterRelation.target_id == tgt.id,
                )
            )).scalar_one_or_none()
            if existing:
                return self._already_exists("关系", f"{src.name} → {tgt.name}", str(existing.id))

            rel = CharacterRelation(
                project_id=pid,
                character_id=src.id,
                target_id=tgt.id,
                rel_type=kwargs.get("rel_type") or "关联",
                label=kwargs.get("label") or "",
                description=kwargs.get("description") or "",
            )
            db.add(rel)
            await db.commit()
            return ToolResult(success=True, data={
                "relation_id": str(rel.id),
                "character_id": str(src.id),
                "character_name": src.name or "",
                "target_id": str(tgt.id),
                "target_name": tgt.name or "",
                "rel_type": rel.rel_type,
                "label": rel.label,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class UpdateRelationTool(BaseTool):
    meta = ToolMeta(
        name="update_relation",
        description="改一条角色关系：关系类型 / 更具体的说法 / 来龙去脉。关系变化的**事实**写进那一章的「本章写成了什么」，"
                    "这里只改描述性文字",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "relation_id": {"type": "string", "description": "关系ID，来自 list_relations"},
                "rel_type": {"type": "string", "description": "关系类型"},
                "label": {"type": "string", "description": "更具体的说法"},
                "description": {"type": "string", "description": "这段关系的来龙去脉"},
            },
            "required": ["relation_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            rid_raw = self._require_param(kwargs, "relation_id")
            if rid_raw is None:
                return self._missing_param("relation_id")
            rel = (await db.execute(
                select(CharacterRelation).where(CharacterRelation.id == uuid.UUID(rid_raw))
            )).scalar_one_or_none()
            if not rel:
                return self._not_found("CharacterRelation")
            await verify_project_owner(db, rel.project_id, kwargs.get("user_id"))
            for field in ("rel_type", "label", "description"):
                if field in kwargs and kwargs[field] is not None:
                    setattr(rel, field, kwargs[field])
            await db.commit()
            names = dict((await db.execute(
                select(Character.id, Character.name).where(Character.project_id == rel.project_id)
            )).all())
            return ToolResult(success=True, data={
                "relation_id": str(rel.id),
                "character_id": str(rel.character_id),
                "character_name": names.get(rel.character_id, ""),
                "target_id": str(rel.target_id),
                "target_name": names.get(rel.target_id, ""),
                "rel_type": rel.rel_type or "",
                "label": rel.label or "",
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class DeleteRelationTool(BaseTool):
    meta = ToolMeta(
        name="delete_relation",
        description="删除一条角色关系记录。两人之间的**关系**不会因此消失，只是不再有一条记录描述它——不确定的话先别删",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        needs_confirmation=True,
        parameters={
            "type": "object",
            "properties": {
                "relation_id": {"type": "string", "description": "要删除的关系ID，来自 list_relations"},
            },
            "required": ["relation_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            rid_raw = self._require_param(kwargs, "relation_id")
            if rid_raw is None:
                return self._missing_param("relation_id")
            rel = (await db.execute(
                select(CharacterRelation).where(CharacterRelation.id == uuid.UUID(rid_raw))
            )).scalar_one_or_none()
            if not rel:
                return self._not_found("CharacterRelation")
            await verify_project_owner(db, rel.project_id, kwargs.get("user_id"))
            rel_id = rel.id
            await db.delete(rel)
            await db.commit()
            return ToolResult(success=True, data={"deleted_relation_id": str(rel_id)})
        except Exception as e:
            await db.rollback()
            return self._err(e)
