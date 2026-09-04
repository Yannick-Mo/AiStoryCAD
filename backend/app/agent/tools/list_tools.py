from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.storycad.models import Chapter, Scene, ChapterEdge, Character, CharacterRelation
from app.utils import row_to_dict


def _decorate_relations(rels, char_names):
    """Light relation rows (browse/network level): identity + type + numeric
    markers only — the long description text belongs to read_relation."""
    rows = []
    for r in rels:
        rows.append({
            "id": str(r.id),
            "character_id": str(r.character_id),
            "character_name": char_names.get(str(r.character_id), "?"),
            "target_id": str(r.target_id),
            "target_name": char_names.get(str(r.target_id), "?"),
            "rel_type": r.rel_type or "",
            "label": r.label or "",
            "trust": r.trust if r.trust is not None else 50,
            "threat": r.threat if r.threat is not None else 50,
            "attraction": r.attraction if r.attraction is not None else 50,
        })
    return rows


class ListRelationsTool(BaseTool):
    meta = ToolMeta(
        name="list_relations",
        description="浏览全项目角色关系网络：每行 = 双方角色名/id、关系类型、标签、信任/威胁/吸引力数值(0-100)。"
                    "用于了解整体人物关系格局。不含关系说明全文——需要某条关系的完整背景用 read_relation(relation_id)",
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
            rels_result = await db.execute(
                select(CharacterRelation).where(CharacterRelation.project_id == pid)
            )
            rels = rels_result.scalars().all()
            chars_result = await db.execute(
                select(Character.id, Character.name).where(Character.project_id == pid)
            )
            char_names = {str(c.id): c.name for c in chars_result}
            return ToolResult(success=True, data={
                "relations": _decorate_relations(rels, char_names),
                "total": len(rels),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ListCharacterRelationsTool(BaseTool):
    meta = ToolMeta(
        name="list_character_relations",
        description="列出某角色参与的全部关系（主动与被动两端）：每行 = 对方角色名/id、关系类型、标签、"
                    "信任/威胁/吸引力数值(0-100)。写该角色互动戏前调用，确认他与每个人的关系。"
                    "需要某条关系的完整说明用 read_relation",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "角色ID，来自 list_characters"},
            },
            "required": ["character_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            char_id = uuid.UUID(kwargs["character_id"])
            rels_result = await db.execute(
                select(CharacterRelation).where(
                    CharacterRelation.project_id == pid,
                    (CharacterRelation.character_id == char_id) |
                    (CharacterRelation.target_id == char_id),
                )
            )
            rels = rels_result.scalars().all()
            chars_result = await db.execute(
                select(Character.id, Character.name).where(Character.project_id == pid)
            )
            char_names = {str(c.id): c.name for c in chars_result}
            return ToolResult(success=True, data={
                "relations": _decorate_relations(rels, char_names),
                "total": len(rels),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadRelationTool(BaseTool):
    meta = ToolMeta(
        name="read_relation",
        description="精读单条角色关系：完整数据（双方角色名/id、类型、标签、信任/威胁/吸引力数值、说明全文）。"
                    "写互动戏需要某段关系的完整背景细节时用。relation_id 来自 list_relations 或 list_character_relations",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "relation_id": {"type": "string", "description": "关系ID，来自 list_relations / list_character_relations 返回结果"},
            },
            "required": ["relation_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            rel_id = uuid.UUID(kwargs["relation_id"])
            rel_result = await db.execute(
                select(CharacterRelation).where(
                    CharacterRelation.project_id == pid,
                    CharacterRelation.id == rel_id,
                )
            )
            rel = rel_result.scalar_one_or_none()
            if not rel:
                return self._not_found("Relation in project")
            chars_result = await db.execute(
                select(Character.id, Character.name).where(Character.project_id == pid)
            )
            char_names = {str(c.id): c.name for c in chars_result}
            d = row_to_dict(rel)
            d["character_name"] = char_names.get(str(rel.character_id), "?")
            d["target_name"] = char_names.get(str(rel.target_id), "?")
            return ToolResult(success=True, data={"relations": [d], "total": 1})
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ReadChapterScenesTool(BaseTool):
    meta = ToolMeta(
        name="read_chapter_scenes",
        description="列出某章内全部场景的轻量清单（导航用）：每场 = ID/标题/序号/POV角色/字数/是否已写。"
                    "不含蓝图全文——拿到场景 ID 后如需要蓝图/正文请用 read_scene / read_scene_content。"
                    "chapter_id 来自 read_chapters（范围）或项目框架结构概览",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 read_chapters（范围读取）或项目框架结构概览"},
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_id = uuid.UUID(kwargs["chapter_id"])
            ch_result = await db.execute(select(Chapter).where(Chapter.id == ch_id))
            chapter = ch_result.scalar_one_or_none()
            if not chapter:
                return self._not_found("Chapter")
            await verify_project_owner(db, chapter.project_id, kwargs.get("user_id"))
            scenes_result = await db.execute(
                select(Scene).where(Scene.chapter_id == ch_id).order_by(Scene.sort_order)
            )
            scenes = [
                {
                    "id": str(s.id),
                    "title": s.title,
                    "sort_order": s.sort_order,
                    "pov_character": s.pov_character or "",
                    "word_count": s.word_count or 0,
                    "written": bool((s.word_count or 0) > 0),
                }
                for s in scenes_result.scalars().all()
            ]
            return ToolResult(success=True, data={
                "chapter_id": str(ch_id),
                "chapter_title": chapter.title or "",
                "scenes": scenes,
                "total": len(scenes),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ListEdgesTool(BaseTool):
    meta = ToolMeta(
        name="list_edges",
        description="列出项目中所有章节连线（剧情流向）：每行 = 连线ID/源章→目标章(标题与ID)/类型(timeline/causal/foreshadow/character)/标签。"
                    "用于确认剧情流向、查因果与伏笔链",
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

            edges_result = await db.execute(
                select(ChapterEdge).where(ChapterEdge.project_id == pid)
            )
            edges = edges_result.scalars().all()

            # Load chapter titles
            ch_ids = set()
            for e in edges:
                ch_ids.add(e.source_id)
                ch_ids.add(e.target_id)
            ch_map = {}
            if ch_ids:
                ch_result = await db.execute(
                    select(Chapter.id, Chapter.title).where(Chapter.id.in_(list(ch_ids)))
                )
                ch_map = {str(c.id): c.title for c in ch_result}

            result_data = []
            for e in edges:
                result_data.append({
                    "id": str(e.id),
                    "source_id": str(e.source_id),
                    "source_title": ch_map.get(str(e.source_id), "?"),
                    "target_id": str(e.target_id),
                    "target_title": ch_map.get(str(e.target_id), "?"),
                    "edge_type": e.edge_type or "",
                    "label": e.label or "",
                })

            return ToolResult(success=True, data={
                "edges": result_data,
                "total": len(result_data),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class SearchNodesTool(BaseTool):
    meta = ToolMeta(
        name="search_nodes",
        description="搜索项目中的节点（场景、章节、角色），支持按关键词搜索标题和摘要",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "node_type": {
                    "type": "string",
                    "description": "节点类型：scene/chapter/character/all（默认all）",
                    "enum": ["scene", "chapter", "character", "all"],
                },
                "limit": {"type": "integer", "description": "每类最多返回条数（默认10）"},
            },
            "required": ["keyword"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            keyword = self._require_param(kwargs, "keyword")
            if keyword is None:
                return self._missing_param("keyword")
            keyword = keyword.strip()
            if not keyword:
                return ToolResult(success=False, error="关键词不能为空")

            node_type = kwargs.get("node_type", "all")
            limit = min(int(kwargs.get("limit", 10) or 10), 50)

            results: dict[str, list] = {}
            kw_like = f"%{keyword}%"

            if node_type in ("scene", "all"):
                sc_result = await db.execute(
                    select(Scene)
                    .where(Scene.project_id == pid)
                    .where(
                        Scene.title.ilike(kw_like) |
                        Scene.summary.ilike(kw_like) |
                        Scene.setting.ilike(kw_like)
                    )
                    .order_by(Scene.sort_order)
                    .limit(limit)
                )
                scenes = sc_result.scalars().all()
                results["scenes"] = [
                    {
                        "id": str(sc.id),
                        "title": sc.title,
                        "chapter_id": str(sc.chapter_id),
                        "summary": (sc.summary or "")[:500],
                        "type": "scene",
                    }
                    for sc in scenes
                ]

            if node_type in ("chapter", "all"):
                ch_result = await db.execute(
                    select(Chapter)
                    .where(Chapter.project_id == pid)
                    .where(
                        Chapter.title.ilike(kw_like) |
                        Chapter.goal.ilike(kw_like)
                    )
                    .order_by(Chapter.sort_order)
                    .limit(limit)
                )
                chapters = ch_result.scalars().all()
                results["chapters"] = [
                    {
                        "id": str(ch.id),
                        "title": ch.title,
                        "act_id": str(ch.act_id) if ch.act_id else "",
                        "goal_preview": (ch.goal or "")[:100],
                        "type": "chapter",
                    }
                    for ch in chapters
                ]

            if node_type in ("character", "all"):
                char_result = await db.execute(
                    select(Character)
                    .where(Character.project_id == pid)
                    .where(
                        Character.name.ilike(kw_like) |
                        Character.personality.ilike(kw_like) |
                        Character.background.ilike(kw_like)
                    )
                    .order_by(Character.sort_order)
                    .limit(limit)
                )
                chars = char_result.scalars().all()
                results["characters"] = [
                    {
                        "id": str(c.id),
                        "name": c.name,
                        "role": c.role or "",
                        "type": "character",
                    }
                    for c in chars
                ]

            total = sum(len(v) for v in results.values())
            return ToolResult(success=True, data={
                "results": results,
                "total": total,
                "keyword": keyword,
                "node_type": node_type,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)
