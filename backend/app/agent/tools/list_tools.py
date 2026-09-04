from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.storycad.models import Chapter, Scene, ChapterEdge, Character, CharacterRelation
from app.utils import row_to_dict


class ListRelationsTool(BaseTool):
    meta = ToolMeta(
        name="list_relations",
        description="读取角色关系数据。每行关系包含：双方角色名/id、关系类型与标签、说明、信任/威胁/吸引力数值(0-100)。"
                    "用法区分："
                    "① 不带参数 = 浏览全项目关系网络（了解整体人物关系格局，适合开局摸底）；"
                    "② 带 character_id = 精读某角色的关系网（写该角色互动戏前调用，确认他与每个人的关系数值）；"
                    "③ 带 relation_id = 精读单条关系（返回该条完整数据，含详细说明全文——需要某段关系的完整背景细节时用）；"
                    "④ 带 rel_type = 只看某类关系（如敌对/亲情），配合 character_id 可缩小网络。"
                    "②③④都会缩小结果集，数据量大时优先使用。若要确认关系说明全文请用 ③",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "精读某角色的关系网：只返回该角色参与的关系（含该角色主动与被动两端）"},
                "relation_id": {"type": "string", "description": "精读单条关系：只返回这条关系的完整数据（说明全文不截断）。relation_id 来自本工具返回结果"},
                "rel_type": {"type": "string", "description": "按关系类型过滤（如 敌对/亲情/师生/好友），可与 character_id 组合缩小结果"},
            },
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            q = select(CharacterRelation).where(CharacterRelation.project_id == pid)
            if kwargs.get("relation_id"):
                # 单条精读：只按项目+ID 过滤，返回完整行
                q = q.where(CharacterRelation.id == uuid.UUID(kwargs["relation_id"]))
            else:
                if kwargs.get("character_id"):
                    char_id = uuid.UUID(kwargs["character_id"])
                    q = q.where(
                        (CharacterRelation.character_id == char_id) |
                        (CharacterRelation.target_id == char_id)
                    )
                if kwargs.get("rel_type"):
                    q = q.where(CharacterRelation.rel_type == str(kwargs["rel_type"]))
            rels_result = await db.execute(q)
            rels = rels_result.scalars().all()

            # Load character names
            chars_result = await db.execute(
                select(Character.id, Character.name).where(Character.project_id == pid)
            )
            char_names = {str(c.id): c.name for c in chars_result}

            result_data = []
            for r in rels:
                d = row_to_dict(r)
                d["character_name"] = char_names.get(str(r.character_id), "?")
                d["target_name"] = char_names.get(str(r.target_id), "?")
                result_data.append(d)

            return ToolResult(success=True, data={
                "relations": result_data,
                "total": len(result_data),
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ListEdgesTool(BaseTool):
    meta = ToolMeta(
        name="list_edges",
        description="列出项目中所有章节连线（剧情流向）",
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
                d = row_to_dict(e)
                d["source_title"] = ch_map.get(str(e.source_id), "?")
                d["target_title"] = ch_map.get(str(e.target_id), "?")
                result_data.append(d)

            return ToolResult(success=True, data={
                "edges": result_data,
                "total": len(result_data),
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


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
            return ToolResult(success=False, error=str(e))
