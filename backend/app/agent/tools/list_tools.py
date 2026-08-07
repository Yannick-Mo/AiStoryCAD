from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.storycad.models import Act, Chapter, Scene, ChapterEdge, Character, CharacterRelation
from app.utils import row_to_dict


class ListChaptersTool(BaseTool):
    meta = ToolMeta(
        name="list_chapters",
        description="列出项目中所有章节的结构概览，包括所属幕、场景数量",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "act_id": {"type": "string", "description": "按幕筛选（可选），幕ID来自 read_full_project 结构概览"},
            },
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            act_id = None
            if kwargs.get("act_id"):
                act_id = uuid.UUID(kwargs["act_id"])

            # Load acts for display
            acts_result = await db.execute(
                select(Act).where(Act.project_id == pid).order_by(Act.sort_order)
            )
            all_acts = {a.id: a for a in acts_result.scalars().all()}

            # Load chapters
            q = select(Chapter).where(Chapter.project_id == pid).order_by(Chapter.sort_order)
            if act_id:
                q = q.where(Chapter.act_id == act_id)
            ch_result = await db.execute(q)
            chapters = ch_result.scalars().all()

            # Load scenes for counts
            chapter_ids = [ch.id for ch in chapters]
            scene_counts: dict[uuid.UUID, int] = {ch.id: 0 for ch in chapters}
            if chapter_ids:
                from sqlalchemy import func
                count_result = await db.execute(
                    select(Scene.chapter_id, func.count(Scene.id))
                    .where(Scene.chapter_id.in_(chapter_ids))
                    .group_by(Scene.chapter_id)
                )
                for ch_id_fk, cnt in count_result:
                    scene_counts[ch_id_fk] = cnt

            result_data = []
            for ch in chapters:
                act_name = all_acts[ch.act_id].name if ch.act_id and ch.act_id in all_acts else ""
                result_data.append({
                    "id": str(ch.id),
                    "title": ch.title,
                    "act_name": act_name,
                    "act_id": str(ch.act_id) if ch.act_id else "",
                    "sort_order": ch.sort_order,
                    "goal_preview": (ch.goal or "")[:100],
                    "status": ch.status or "",
                    "scene_count": scene_counts.get(ch.id, 0),
                })

            return ToolResult(success=True, data={
                "chapters": result_data,
                "total": len(result_data),
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ListScenesTool(BaseTool):
    meta = ToolMeta(
        name="list_scenes",
        description="列出项目中的场景，可按章节筛选，返回标题、POV、摘要",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "按章节筛选（可选），章节ID来自 list_chapters 或 read_full_project"},
            },
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            q = select(Scene).where(Scene.project_id == pid).order_by(Scene.sort_order)
            if kwargs.get("chapter_id"):
                q = q.where(Scene.chapter_id == uuid.UUID(kwargs["chapter_id"]))

            sc_result = await db.execute(q)
            scenes = sc_result.scalars().all()

            result_data = []
            for sc in scenes:
                result_data.append({
                    "id": str(sc.id),
                    "title": sc.title,
                    "chapter_id": str(sc.chapter_id),
                    "sort_order": sc.sort_order,
                    "summary": (sc.summary or "")[:500],
                    "pov_character": sc.pov_character or "",
                    "setting": sc.setting or "",
                    "scene_time": sc.scene_time or "",
                    "word_count": sc.word_count or 0,
                })

            return ToolResult(success=True, data={
                "scenes": result_data,
                "total": len(result_data),
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ListRelationsTool(BaseTool):
    meta = ToolMeta(
        name="list_relations",
        description="列出项目中所有角色关系，可按角色筛选",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "按角色筛选（可选），只返回该角色参与的关系"},
            },
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))

            q = select(CharacterRelation).where(CharacterRelation.project_id == pid)
            if kwargs.get("character_id"):
                char_id = uuid.UUID(kwargs["character_id"])
                q = q.where(
                    (CharacterRelation.character_id == char_id) |
                    (CharacterRelation.target_id == char_id)
                )
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
