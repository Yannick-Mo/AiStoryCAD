from __future__ import annotations

import json
import logging
import time
import uuid
from collections import OrderedDict, defaultdict
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.rag import RAGEngine
from app.knowledge.skill_engine import _shared_engine as _shared_skill_engine
from app.project.models import Project, ProjectConfig
from app.storycad.models import (
    Act,
    Chapter,
    ChapterEdge,
    Character,
    CharacterRelation,
    Scene,
    SceneContent,
)
from app.utils import row_to_dict

logger = logging.getLogger(__name__)


class _UUIDEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, uuid.UUID):
            return str(o)
        return super().default(o)

_CONTEXT_CACHE_TTL = 300
_CONTEXT_CACHE_MAX_SIZE = 100
_REDIS_CACHE_PREFIX = "ctx_cache:"


class _LRUCache:
    """In-memory LRU fallback cache used when Redis is unavailable."""

    def __init__(self, ttl: int, maxsize: int):
        self._ttl = ttl
        self._maxsize = maxsize
        self._store: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def get(self, key: str) -> dict | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return data

    def set(self, key: str, data: dict) -> None:
        self._store[key] = (time.monotonic(), data)
        self._store.move_to_end(key)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def delete_prefix(self, prefix: str) -> None:
        """Drop every cached entry whose key starts with *prefix*."""
        stale = [k for k in self._store if k.startswith(prefix)]
        for k in stale:
            del self._store[k]


_CONTEXT_CACHE = _LRUCache(ttl=_CONTEXT_CACHE_TTL, maxsize=_CONTEXT_CACHE_MAX_SIZE)


def _is_meaningful_query(query_hint: str) -> bool:
    if len(query_hint) < 10:
        return False
    stripped = query_hint.strip().lower()
    greetings = {"hi", "hello", "hey", "你好", "您好", "嗨", "哈喽", "nihao", "hi there", "hello there"}
    if stripped in greetings or stripped.rstrip("?!。，,.") in greetings:
        return False
    short_patterns = {"嗯", "是", "好", "ok", "yes", "no", "y", "n", "哦", "嗯嗯", "好的", "是的"}
    if stripped in short_patterns:
        return False
    meaningful_keywords = {"故事", "小说", "情节", "角色", "人物", "剧情", "大纲", "章节", "场景", "写作", "设定", "世界观", "主题", "对话", "描述", "开头", "结尾", "转折", "高潮", "plot", "character", "story", "writing", "outline", "chapter", "scene", "theme", "protagonist", "antagonist", "motivation", "conflict", "pacing", "dialogue", "narrative", "setting", "worldbuild", "genre", "tone", "mood"}
    if any(kw in stripped for kw in meaningful_keywords):
        return True
    return len(query_hint) >= 15


class ContextBuilder:
    def __init__(self, db: AsyncSession, redis_client: Redis | None = None):
        self.db = db
        self._redis = redis_client
        self._rag_engine: RAGEngine | None = None

    @property
    def skill_engine(self):
        return _shared_skill_engine

    @property
    def rag_engine(self) -> RAGEngine:
        if self._rag_engine is None:
            self._rag_engine = RAGEngine(self.db)
        return self._rag_engine

    # ------------------------------------------------------------------
    # Cache  — Redis primary, in-memory _LRUCache as fallback
    # ------------------------------------------------------------------

    def _cache_key(self, project_id: uuid.UUID, kind: str, depth: str = "") -> str:
        return f"{_REDIS_CACHE_PREFIX}{project_id}:{kind}:{depth}"

    async def _cache_get(self, key: str) -> dict | None:
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                if raw is not None:
                    data = raw.decode() if isinstance(raw, bytes) else raw
                    return json.loads(data)
            except json.JSONDecodeError:
                logger.warning("Redis cache data corrupted for key=%s, falling back to in-memory cache", key)
            except Exception as exc:
                logger.warning("Redis cache get failed for key=%s: %s", key, exc)
            return _CONTEXT_CACHE.get(key)
        return _CONTEXT_CACHE.get(key)

    async def _cache_set(self, key: str, data: dict) -> None:
        if self._redis is not None:
            try:
                raw = json.dumps(data, ensure_ascii=False, cls=_UUIDEncoder)
                await self._redis.setex(key, _CONTEXT_CACHE_TTL, raw)
            except Exception as exc:
                logger.warning("Redis cache set failed for key=%s: %s", key, exc)
            _CONTEXT_CACHE.set(key, data)
        else:
            _CONTEXT_CACHE.set(key, data)

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    @staticmethod
    def invalidate_project(project_id: uuid.UUID) -> None:
        """Drop all cached context for a project (synchronous, in-memory).

        Must be called after a successful write so read tools (e.g.
        ``read_chapters`` / ``read_scene``) don't serve a stale skeleton from
        the shared 300s cache within the same turn.
        """
        prefix = _REDIS_CACHE_PREFIX + str(project_id) + ":"
        _CONTEXT_CACHE.delete_prefix(prefix)

    async def _invalidate_redis_project(self, project_id: uuid.UUID) -> None:
        """Best-effort Redis cache invalidation for a project."""
        if self._redis is None:
            return
        try:
            pattern = _REDIS_CACHE_PREFIX + str(project_id) + ":*"
            async for key in self._redis.scan_iter(match=pattern, count=100):
                await self._redis.delete(key)
        except Exception as exc:
            logger.warning("Redis cache invalidation failed for project=%s: %s", project_id, exc)

    async def ainvalidate_project(self, project_id: uuid.UUID) -> None:
        """Invalidate both Redis and in-memory caches for a project."""
        self.invalidate_project(project_id)
        await self._invalidate_redis_project(project_id)

    # ------------------------------------------------------------------
    # Shared project tree loader (used by build_summary)
    # ------------------------------------------------------------------

    async def _load_project_tree(
        self,
        project_id: uuid.UUID,
        limit_chapters: int = 3500,
        limit_scenes: int = 3500,
    ) -> dict:
        acts_result = await self.db.execute(
            select(Act).where(Act.project_id == project_id).order_by(Act.sort_order)
        )
        acts = acts_result.scalars().all()

        chapter_total_result = await self.db.execute(
            select(func.count()).select_from(Chapter).where(Chapter.project_id == project_id)
        )
        chapter_total = chapter_total_result.scalar_one() or 0
        chapters_result = await self.db.execute(
            select(Chapter).where(Chapter.project_id == project_id)
            .order_by(Chapter.sort_order).limit(limit_chapters)
        )
        all_chapters = chapters_result.scalars().all()
        chapter_truncated = chapter_total > len(all_chapters)
        chapters_by_act: dict[uuid.UUID, list] = {}
        for ch in all_chapters:
            chapters_by_act.setdefault(ch.act_id, []).append(ch)

        chapter_ids = [ch.id for ch in all_chapters]
        scene_total_result = await self.db.execute(
            select(func.count()).select_from(Scene).where(Scene.chapter_id.in_(chapter_ids))
        )
        scene_total = scene_total_result.scalar_one() or 0
        scenes_result = await self.db.execute(
            select(Scene).where(Scene.chapter_id.in_(chapter_ids))
            .order_by(Scene.sort_order).limit(limit_scenes)
        )
        all_scenes = scenes_result.scalars().all()
        scene_truncated = scene_total > len(all_scenes)
        scenes_by_chapter: dict[uuid.UUID, list] = {}
        for sc in all_scenes:
            scenes_by_chapter.setdefault(sc.chapter_id, []).append(sc)

        return {
            "acts": acts,
            "all_chapters": all_chapters,
            "chapters_by_act": chapters_by_act,
            "all_scenes": all_scenes,
            "scenes_by_chapter": scenes_by_chapter,
            "chapter_ids": chapter_ids,
            "chapter_total": chapter_total,
            "scene_total": scene_total,
            "chapter_truncated": chapter_truncated,
            "scene_truncated": scene_truncated,
        }

    @staticmethod
    def _truncation_note(tree: dict) -> str:
        """Honest marker when the skeleton caps silently cut off chapters/scenes."""
        if not (tree.get("chapter_truncated") or tree.get("scene_truncated")):
            return ""
        return (
            f"（项目规模较大：共 {tree.get('chapter_total', '?')} 章 / "
            f"{tree.get('scene_total', '?')} 场，本次仅列出前 "
            f"{len(tree.get('all_chapters', []))} 章 / {len(tree.get('all_scenes', []))} 场，"
            f"其余未在列表中显示）"
        )
    # ------------------------------------------------------------------
    # Focused data builders — analysis tools' material source.  These never
    # dump the whole project: each one assembles exactly the data a single
    # analysis object (chapter / character / writing progress) needs.
    # ------------------------------------------------------------------

    _ANALYSIS_BODY_CHARS = 60_000  # chapter-level cap for full scene body text

    async def build_chapter_focus(
        self, project_id: uuid.UUID, chapter_id: uuid.UUID
    ) -> dict | None:
        """Material for a single-chapter analysis.

        Returns the chapter, ALL of its scenes with full body text (not
        truncated previews), its act, and the nearest neighbouring chapters
        (title + goal) for structural context.  ``None`` if the chapter does
        not exist in this project.
        """
        result = await self.db.execute(
            select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
        )
        chapter = result.scalar_one_or_none()
        if not chapter:
            return None

        scenes_result = await self.db.execute(
            select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.sort_order)
        )
        scenes = scenes_result.scalars().all()

        # Single batched query for all body text — no N+1.
        body_by_scene: dict[uuid.UUID, str] = {}
        if scenes:
            content_result = await self.db.execute(
                select(SceneContent.scene_id, SceneContent.content).where(
                    SceneContent.scene_id.in_([s.id for s in scenes])
                )
            )
            body_by_scene = {sid: (body or "") for sid, body in content_result.all()}

        act_data = None
        if chapter.act_id:
            act_result = await self.db.execute(select(Act).where(Act.id == chapter.act_id))
            act = act_result.scalar_one_or_none()
            act_data = row_to_dict(act) if act else None

        scenes_data = []
        total_chars = 0
        for sc in scenes:
            body = body_by_scene.get(sc.id, "")
            total_chars += len(body)
            scenes_data.append({
                "id": str(sc.id),
                "title": sc.title or "",
                "pov_character": sc.pov_character or "",
                "setting": sc.setting or "",
                "scene_time": sc.scene_time or "",
                "summary": sc.summary or "",
                "sort_order": sc.sort_order,
                "content": body,
            })
        content_truncated = False
        if total_chars > self._ANALYSIS_BODY_CHARS:
            # Keep earlier scenes whole; cut the tail at scene boundaries.
            content_truncated = True
            remaining = self._ANALYSIS_BODY_CHARS
            for entry in scenes_data:
                n = len(entry["content"])
                if remaining <= 0:
                    entry["content"] = ""
                    entry["content_cut"] = True
                elif n > remaining:
                    entry["content"] = entry["content"][:remaining]
                    entry["content_cut"] = True
                    remaining = 0
                else:
                    remaining -= n

        prev_ch = await self._neighbour_chapter(project_id, chapter, -1)
        next_ch = await self._neighbour_chapter(project_id, chapter, 1)

        return {
            "chapter": {
                "id": str(chapter.id),
                "title": chapter.title or "",
                "goal": chapter.goal or "",
                "status": chapter.status or "",
                "sort_order": chapter.sort_order,
                "act_id": str(chapter.act_id) if chapter.act_id else "",
            },
            "act": act_data,
            "prev_chapter": prev_ch,
            "next_chapter": next_ch,
            "scenes": scenes_data,
            "scene_count": len(scenes_data),
            "body_chars": total_chars,
            "content_truncated": content_truncated,
        }

    async def _neighbour_chapter(
        self, project_id: uuid.UUID, chapter: Chapter, direction: int
    ) -> dict | None:
        """Adjacent chapter in story order (same act first, then across the
        act boundary).  ``direction``: -1 previous, +1 next."""
        result = await self.db.execute(
            select(Chapter)
            .where(
                Chapter.project_id == project_id,
                Chapter.act_id == chapter.act_id,
                Chapter.sort_order == (chapter.sort_order or 0) + direction,
            )
            .limit(1)
        )
        nbr = result.scalar_one_or_none()
        if nbr:
            return {"title": nbr.title or "", "goal": (nbr.goal or "")[:800],
                    "sort_order": nbr.sort_order}

        act_result = await self.db.execute(select(Act).where(Act.id == chapter.act_id))
        act = act_result.scalar_one_or_none()
        if not act:
            return None
        if direction < 0:
            act_q = (select(Act).where(Act.project_id == project_id,
                                       Act.sort_order < (act.sort_order or 0))
                     .order_by(Act.sort_order.desc()).limit(1))
        else:
            act_q = (select(Act).where(Act.project_id == project_id,
                                       Act.sort_order > (act.sort_order or 0))
                     .order_by(Act.sort_order.asc()).limit(1))
        other = (await self.db.execute(act_q)).scalar_one_or_none()
        if not other:
            return None
        if direction < 0:
            ch_q = (select(Chapter).where(Chapter.project_id == project_id,
                                          Chapter.act_id == other.id)
                    .order_by(Chapter.sort_order.desc()).limit(1))
        else:
            ch_q = (select(Chapter).where(Chapter.project_id == project_id,
                                          Chapter.act_id == other.id)
                    .order_by(Chapter.sort_order.asc()).limit(1))
        nbr2 = (await self.db.execute(ch_q)).scalar_one_or_none()
        if not nbr2:
            return None
        return {"title": nbr2.title or "", "goal": (nbr2.goal or "")[:800],
                "sort_order": nbr2.sort_order}

    async def build_character_focus(
        self, project_id: uuid.UUID, character_id: uuid.UUID
    ) -> dict | None:
        """Material for a character-arc analysis: full profile, on-page
        appearances (POV scenes matched leniently by name, ordered by
        act → chapter → scene, with body previews), and relations.
        ``None`` if the character does not exist in this project.
        """
        result = await self.db.execute(
            select(Character).where(Character.id == character_id,
                                    Character.project_id == project_id)
        )
        char = result.scalar_one_or_none()
        if not char:
            return None
        name = char.name or ""
        if not name:
            return {
                "character": row_to_dict(char),
                "appearances": [],
                "appearance_count": 0,
                "relations": [],
            }

        scenes_q = (
            select(
                Scene.id, Scene.chapter_id, Scene.title, Scene.sort_order,
                Scene.scene_time, Scene.setting, Scene.pov_character, Scene.summary,
                Chapter.title.label("chapter_title"),
                Chapter.sort_order.label("chapter_order"),
                Act.sort_order.label("act_order"),
            )
            .join(Chapter, Chapter.id == Scene.chapter_id)
            .join(Act, Act.id == Chapter.act_id)
            .where(Scene.project_id == project_id)
            .where(Scene.pov_character.ilike(f"%{name}%"))
            .order_by(Act.sort_order.asc(), Chapter.sort_order.asc(), Scene.sort_order.asc())
            .limit(60)
        )
        rows = (await self.db.execute(scenes_q)).all()

        body_by_scene: dict[uuid.UUID, str] = {}
        scene_ids = [r.id for r in rows]
        if scene_ids:
            content_result = await self.db.execute(
                select(SceneContent.scene_id, SceneContent.content).where(
                    SceneContent.scene_id.in_(scene_ids)
                )
            )
            body_by_scene = {sid: (body or "") for sid, body in content_result.all()}

        appearances = []
        for r in rows:
            body = body_by_scene.get(r.id, "")
            appearances.append({
                "scene_id": str(r.id),
                "chapter_id": str(r.chapter_id) if r.chapter_id else "",
                "chapter_title": r.chapter_title or "",
                "chapter_order": r.chapter_order,
                "act_order": r.act_order,
                "scene_title": r.title or "",
                "scene_order": r.sort_order,
                "scene_time": r.scene_time or "",
                "setting": r.setting or "",
                "pov_character": r.pov_character or "",
                "summary": r.summary or "",
                "body_preview": body[:800],
                "body_len": len(body),
            })

        rels_result = await self.db.execute(
            select(CharacterRelation).where(
                CharacterRelation.project_id == project_id,
                (CharacterRelation.character_id == character_id)
                | (CharacterRelation.target_id == character_id),
            )
        )
        rels = rels_result.scalars().all()
        char_ids = {character_id}
        for rel in rels:
            char_ids.add(rel.character_id)
            char_ids.add(rel.target_id)
        names: dict[uuid.UUID, str] = {}
        if char_ids:
            names_result = await self.db.execute(
                select(Character.id, Character.name).where(Character.id.in_(list(char_ids)))
            )
            names = {cid: nm for cid, nm in names_result.all()}
        relations_data = [{
            "character_name": names.get(rel.character_id, str(rel.character_id)),
            "target_name": names.get(rel.target_id, str(rel.target_id)),
            "rel_type": rel.rel_type or "",
            "label": rel.label or "",
            "description": (rel.description or "")[:300],
            "trust": rel.trust or 0,
            "threat": rel.threat or 0,
            "attraction": rel.attraction or 0,
        } for rel in rels]

        return {
            "character": {
                "id": str(char.id),
                "name": char.name or "",
                "role": char.role or "",
                "personality": char.personality or "",
                "appearance": char.appearance or "",
                "background": char.background or "",
                "motivation": char.motivation or "",
            },
            "appearances": appearances,
            "appearance_count": len(appearances),
            "relations": relations_data,
        }

    async def build_writing_progress(self, project_id: uuid.UUID) -> dict:
        """Compact project state for ``suggest_next``: aggregate counts,
        the most recently written scenes (by SceneContent.updated_at), and
        the next unwritten candidates in story order."""
        count_q = (
            select(
                func.count().select_from(Act).where(Act.project_id == project_id),
                func.count().select_from(Chapter).where(Chapter.project_id == project_id),
                func.count().select_from(Scene).where(Scene.project_id == project_id),
            )
        )
        row = (await self.db.execute(count_q)).first()
        total_acts, total_chapters, total_scenes = (int(v) for v in row) if row else (0, 0, 0)

        written_ids: set[uuid.UUID] = set()
        written_result = await self.db.execute(
            select(SceneContent.scene_id).where(
                SceneContent.project_id == project_id,
                SceneContent.content.isnot(None),
                SceneContent.content != "",
            )
        )
        written_ids = {sid for (sid,) in written_result.all()}

        # Recently written (by edit time)
        recent_rows = await self.db.execute(
            select(SceneContent.scene_id)
            .where(SceneContent.project_id == project_id,
                   SceneContent.content.isnot(None),
                   SceneContent.content != "")
            .order_by(SceneContent.updated_at.desc())
            .limit(5)
        )
        recent_ids = [sid for (sid,) in recent_rows.all()]

        # Story-ordered light rows for locating scenes
        ordered_q = (
            select(
                Scene.id, Scene.chapter_id, Scene.title,
                Chapter.id.label("chapter_id_x"), Chapter.title.label("chapter_title"),
                Chapter.sort_order.label("chapter_order"),
                Act.sort_order.label("act_order"), Act.name.label("act_name"),
            )
            .join(Chapter, Chapter.id == Scene.chapter_id)
            .join(Act, Act.id == Chapter.act_id)
            .where(Scene.project_id == project_id)
            .order_by(Act.sort_order.asc(), Chapter.sort_order.asc(), Scene.sort_order.asc())
        )
        ordered_rows = (await self.db.execute(ordered_q)).all()
        id_to_loc = {r.id: r for r in ordered_rows}

        recent_written = []
        for sid in recent_ids:
            r = id_to_loc.get(sid)
            if r:
                recent_written.append({
                    "act": r.act_name or "",
                    "chapter": r.chapter_title or "",
                    "scene": r.title or "",
                    "scene_id": str(r.id),
                })

        unwritten_candidates = []
        for r in ordered_rows:
            if r.id in written_ids:
                continue
            unwritten_candidates.append({
                "act": r.act_name or "",
                "chapter": r.chapter_title or "",
                "scene": r.title or "",
                "scene_id": str(r.id),
            })
            if len(unwritten_candidates) >= 20:
                break

        written_scenes = len(written_ids)
        return {
            "total_acts": total_acts,
            "total_chapters": total_chapters,
            "total_scenes": total_scenes,
            "written_scenes": written_scenes,
            "progress_pct": round(written_scenes / total_scenes * 100) if total_scenes else 0,
            "recent_written": recent_written,
            "unwritten_candidates": unwritten_candidates,
        }

    async def build_health_snapshot(self, project_id: uuid.UUID) -> dict:
        """Deterministic whole-project health snapshot (SQL aggregations +
        light projection rows — never loads scene body/blueprint text)."""
        total_acts_result = await self.db.execute(
            select(func.count()).select_from(Act).where(Act.project_id == project_id)
        )
        total_acts = int(total_acts_result.scalar_one() or 0)
        total_chapters_result = await self.db.execute(
            select(func.count()).select_from(Chapter).where(Chapter.project_id == project_id)
        )
        total_chapters = int(total_chapters_result.scalar_one() or 0)
        total_scenes_result = await self.db.execute(
            select(func.count()).select_from(Scene).where(Scene.project_id == project_id)
        )
        total_scenes = int(total_scenes_result.scalar_one() or 0)
        written_result = await self.db.execute(
            select(SceneContent.scene_id).where(
                SceneContent.project_id == project_id,
                SceneContent.content.isnot(None),
                SceneContent.content != "",
            )
        )
        written_ids = {sid for (sid,) in written_result.all()}
        written_scenes = len(written_ids)
        unwritten_count = max(0, total_scenes - written_scenes)

        # Unwritten scene details (first 20 in story order)
        ordered_q = (
            select(
                Scene.id,
                Chapter.title.label("chapter_title"),
                Act.name.label("act_name"),
                Scene.title.label("scene_title"),
            )
            .join(Chapter, Chapter.id == Scene.chapter_id)
            .join(Act, Act.id == Chapter.act_id)
            .where(Scene.project_id == project_id)
            .order_by(Act.sort_order.asc(), Chapter.sort_order.asc(), Scene.sort_order.asc())
        )
        ordered_rows = (await self.db.execute(ordered_q)).all()
        unwritten_scenes = []
        for r in ordered_rows:
            if r.id in written_ids:
                continue
            unwritten_scenes.append({
                "act": r.act_name or "",
                "chapter": r.chapter_title or "",
                "scene": r.scene_title or "",
            })
            if len(unwritten_scenes) >= 20:
                break

        # Chapters without any scene
        chapters_result = await self.db.execute(
            select(Chapter).where(Chapter.project_id == project_id)
        )
        chapters = chapters_result.scalars().all()
        chapter_scene_map: dict[uuid.UUID, int] = defaultdict(int)
        for sc_row in (await self.db.execute(
            select(Scene.chapter_id).where(Scene.project_id == project_id)
        )).all():
            chapter_scene_map[sc_row[0]] += 1
        act_name_map = {}
        for act in (await self.db.execute(
            select(Act).where(Act.project_id == project_id)
        )).scalars().all():
            act_name_map[act.id] = act.name or ""
        empty_chapters = []
        for ch in chapters:
            if chapter_scene_map.get(ch.id, 0) == 0:
                empty_chapters.append({
                    "act": act_name_map.get(ch.act_id, ""),
                    "chapter": ch.title or "",
                })
                if len(empty_chapters) >= 10:
                    break
        empty_chapters_count = sum(1 for ch in chapters if chapter_scene_map.get(ch.id, 0) == 0)

        # Isolated characters (no relations at all)
        chars_result = await self.db.execute(
            select(Character).where(Character.project_id == project_id)
        )
        chars = chars_result.scalars().all()
        rel_endpoints: set[uuid.UUID] = set()
        for rel in (await self.db.execute(
            select(CharacterRelation.character_id, CharacterRelation.target_id).where(
                CharacterRelation.project_id == project_id)
        )).all():
            rel_endpoints.add(rel[0])
            rel_endpoints.add(rel[1])
        isolated_chars = [
            c.name or "" for c in chars if c.id not in rel_endpoints
        ]
        isolated_count = len(isolated_chars)

        edges_result = await self.db.execute(
            select(func.count()).select_from(ChapterEdge).where(
                ChapterEdge.project_id == project_id)
        )
        total_edges = int(edges_result.scalar_one() or 0)

        return {
            "total_acts": total_acts,
            "total_chapters": total_chapters,
            "total_scenes": total_scenes,
            "written_scenes": written_scenes,
            "unwritten_scenes_count": unwritten_count,
            "unwritten_scenes": unwritten_scenes[:20],
            "empty_chapters_count": empty_chapters_count,
            "empty_chapters": empty_chapters[:10],
            "total_characters": len(chars),
            "isolated_characters_count": isolated_count,
            "isolated_characters": isolated_chars[:10],
            "total_edges": total_edges,
        }

    async def build_recent_scenes_hint(
        self, project_id: uuid.UUID, max_scenes: int = 5
    ) -> str | None:
        """Compact hint of the scenes at the tail of the story order (title,
        POV, blueprint snippet): the very last scene gets up to 800 chars,
        the preceding scenes up to 200 chars each.

        Loaded on demand with a single light query instead of carrying every
        scene summary inside the framework tree.
        """
        rows = (await self.db.execute(
            select(Scene.id, Scene.title, Scene.pov_character,
                   Scene.summary, Scene.word_count)
            .join(Chapter, Chapter.id == Scene.chapter_id)
            .join(Act, Act.id == Chapter.act_id)
            .where(Scene.project_id == project_id)
            .order_by(Act.sort_order.desc(), Chapter.sort_order.desc(),
                      Scene.sort_order.desc())
            .limit(max_scenes)
        )).all()
        if not rows:
            return None
        lines = ["\n最近场景快照："]
        last_idx = len(rows) - 1
        for idx, (sid, title, pov, summary, word_count) in enumerate(reversed(rows)):
            cap = 800 if idx == last_idx else 200
            snippet = f"- {title or '?'}(scene_id={sid})"
            if pov:
                snippet += f" [{pov}]"
            snippet += f" {'已写' if (word_count or 0) > 0 else '未写'}"
            if summary:
                snippet += f": {(summary or '')[:cap]}"
            lines.append(snippet)
        return "\n".join(lines)

    async def build_chapter_window(
        self,
        project_id: uuid.UUID,
        chapter_from: int,
        chapter_to: int,
        budget_chars: int = 20000,
    ) -> dict:
        """Range window of chapters by *global* story order (act sort →
        chapter sort, numbering continues across acts).

        Only the requested window's chapter rows (title/status/goal) are
        loaded — never the whole project's text.  The output is budget
        trimmed here (not by the 8000-char executor ceiling) so the
        ``truncated``/``next_from`` markers stay structurally intact.
        """
        ordered_result = await self.db.execute(
            select(Chapter.id)
            .join(Act, Act.id == Chapter.act_id)
            .where(Chapter.project_id == project_id)
            .order_by(Act.sort_order.asc(), Chapter.sort_order.asc())
        )
        ordered_ids = [rid for (rid,) in ordered_result.all()]
        total = len(ordered_ids)

        if chapter_to > total:
            chapter_to = total
        if chapter_from < 1:
            chapter_from = 1

        if chapter_to < chapter_from:
            return {
                "chapter_from": chapter_from,
                "chapter_to": chapter_to,
                "total_chapters": total,
                "act_map": {},
                "chapters": [],
                "truncated": False,
                "next_from": None,
            }

        window_ids = ordered_ids[chapter_from - 1 : chapter_to]

        # Light projection: window rows only (goals included).  Two passes
        # keep the big ORDER BY JOIN away from the full text columns.
        window_result = await self.db.execute(
            select(Chapter).where(Chapter.id.in_(window_ids))
        )
        chapter_map = {ch.id: ch for ch in window_result.scalars().all()}

        act_result = await self.db.execute(
            select(Act.id, Act.name).where(Act.project_id == project_id)
        )
        act_map = {a_id: (a_name or "") for a_id, a_name in act_result.all()}

        entries: list[dict[str, Any]] = []
        truncated = False
        next_from: int | None = None
        used = 0
        for idx, cid in enumerate(window_ids, start=chapter_from):
            ch = chapter_map.get(cid)
            if ch is None:
                continue
            entry: dict[str, Any] = {
                "global_order": idx,
                "id": str(ch.id),
                "title": ch.title or "",
                "sort_order": ch.sort_order,
                "act_id": str(ch.act_id) if ch.act_id else "",
                "act_name": act_map.get(ch.act_id, ""),
                "status": ch.status or "",
                "goal": ch.goal or "",
            }
            est = len(json.dumps(entry, ensure_ascii=False))
            if used + est + 2 > budget_chars:
                truncated = True
                next_from = idx
                break
            entries.append(entry)
            used += est + 2

        return {
            "chapter_from": chapter_from,
            "chapter_to": chapter_to,
            "total_chapters": total,
            "act_map": {str(k): v for k, v in act_map.items()},
            "chapters": entries,
            "truncated": truncated,
            "next_from": next_from,
        }

    async def build_recent_items(
        self,
        project_id: uuid.UUID,
        kind: str = "scenes",
        n: int = 5,
        budget_chars: int = 11000,
    ) -> dict:
        """Most recently written/updated scenes (SceneContent.updated_at
        descending) or their containing chapters — by edit time, NOT story
        position.  Never returns full scene body text.
        """
        recent_limit = n if kind == "scenes" else max(n * 4, 20)
        recent_result = await self.db.execute(
            select(SceneContent.scene_id, SceneContent.updated_at)
            .where(
                SceneContent.project_id == project_id,
                SceneContent.content.isnot(None),
                SceneContent.content != "",
            )
            .order_by(SceneContent.updated_at.desc())
            .limit(recent_limit)
        )
        recent_rows = recent_result.all()
        if not recent_rows:
            if kind == "chapters":
                return {"chapters": [], "truncated": False}
            return {"scenes": [], "truncated": False}

        scene_ids = [sid for (sid, _ts) in recent_rows]

        detail_result = await self.db.execute(
            select(Scene, Chapter.title.label("chapter_title"),
                   Act.name.label("act_name"))
            .join(Chapter, Chapter.id == Scene.chapter_id)
            .join(Act, Act.id == Chapter.act_id)
            .where(Scene.id.in_(scene_ids))
        )
        detail_map: dict[uuid.UUID, dict[str, Any]] = {}
        for sc, chapter_title, act_name in detail_result.all():
            detail_map[sc.id] = {
                "scene": sc,
                "chapter_id": str(sc.chapter_id) if sc.chapter_id else "",
                "chapter_title": chapter_title or "",
                "act_name": act_name or "",
            }

        if kind == "chapters":
            seen: list[uuid.UUID] = []
            for sid, _ts in recent_rows:
                info = detail_map.get(sid)
                if not info:
                    continue
                ch_id_uuid = None
                try:
                    ch_id_uuid = uuid.UUID(info["chapter_id"])
                except (ValueError, TypeError):
                    continue
                if ch_id_uuid not in seen:
                    seen.append(ch_id_uuid)
                if len(seen) >= n:
                    break

            chapter_items: list[dict[str, Any]] = []
            truncated = False
            used = 0
            if seen:
                ch_result = await self.db.execute(
                    select(Chapter).where(Chapter.id.in_(seen))
                )
                ch_map = {ch.id: ch for ch in ch_result.scalars().all()}
                for ch_id_uuid in seen:
                    ch = ch_map.get(ch_id_uuid)
                    if ch is None:
                        continue
                    entry: dict[str, Any] = {
                        "id": str(ch.id),
                        "title": ch.title or "",
                        "status": ch.status or "",
                        "goal": ch.goal or "",
                    }
                    # newest scene inside this chapter
                    for sid, _ts in recent_rows:
                        info = detail_map.get(sid)
                        if info and info["chapter_id"] == str(ch.id):
                            entry["act_name"] = info["act_name"]
                            entry["latest_scene_id"] = str(sid)
                            entry["latest_scene_title"] = info["scene"].title or ""
                            entry["latest_updated_at"] = _ts.isoformat() if hasattr(_ts, "isoformat") else str(_ts)
                            break
                    est = len(json.dumps(entry, ensure_ascii=False))
                    if used + est + 2 > budget_chars:
                        truncated = True
                        break
                    chapter_items.append(entry)
                    used += est + 2
            return {"chapters": chapter_items, "truncated": truncated}

        items: list[dict[str, Any]] = []
        truncated = False
        used = 0
        for sid, ts in recent_rows[:n]:
            info = detail_map.get(sid)
            if info is None:
                continue
            sc = info["scene"]
            entry = {
                "id": str(sc.id),
                "title": sc.title or "",
                "chapter_id": info.get("chapter_id", ""),
                "chapter_title": info.get("chapter_title", ""),
                "act_name": info.get("act_name", ""),
                "sort_order": sc.sort_order,
                "summary_preview": (sc.summary or "")[:500],
                "word_count": sc.word_count or 0,
                "updated_at": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            }
            est = len(json.dumps(entry, ensure_ascii=False))
            if used + est + 2 > budget_chars:
                truncated = True
                break
            items.append(entry)
            used += est + 2
        return {"scenes": items, "truncated": truncated}


    # ------------------------------------------------------------------
    # Build summary (with depth parameter)
    # ------------------------------------------------------------------

    async def build_summary(
        self,
        project_id: uuid.UUID,
        query_hint: str = "",
        depth: str = "minimal",
        skip_cache: bool = False,
    ) -> dict:
        ck = self._cache_key(project_id, "summary", depth)
        if not skip_cache:
            cached = await self._cache_get(ck)
            if cached is not None:
                return dict(cached)

        proj = await self._get_project(project_id)
        if not proj:
            return {}

        tree = await self._load_project_tree(project_id)
        acts = tree["acts"]
        all_chapters = tree["all_chapters"]
        chapters_by_act = tree["chapters_by_act"]
        all_scenes = tree["all_scenes"]
        scenes_by_chapter = tree["scenes_by_chapter"]
        chapter_ids = tree["chapter_ids"]

        acts_data = []
        for act in acts:
            chapters_data = []
            for ch in chapters_by_act.get(act.id, []):
                scenes_data = []
                for sc in scenes_by_chapter.get(ch.id, []):
                    if depth == "framework":
                        # Framework = bare structure tree for the main loop:
                        # scene text fields (summary/pov/setting/time) are NOT
                        # loaded — the loop renderer only shows titles/IDs and
                        # scene goals are read on demand via read_scene.
                        scenes_data.append({
                            "id": str(sc.id),
                            "title": sc.title,
                            "sort_order": sc.sort_order,
                            "word_count": sc.word_count or 0,
                        })
                        continue
                    entry: dict[str, Any] = {
                        "id": str(sc.id),
                        "title": sc.title,
                        "sort_order": sc.sort_order,
                        "summary": (sc.summary or "")[:500],
                        "pov_character": sc.pov_character or "",
                    }
                    if depth == "summary":
                        entry["setting"] = sc.setting or ""
                        entry["scene_time"] = sc.scene_time or ""
                        entry["summary"] = (sc.summary or "")[:1000]
                    scenes_data.append(entry)

                ch_entry: dict[str, Any] = {
                    "id": str(ch.id),
                    "title": ch.title,
                    "sort_order": ch.sort_order,
                    "scenes": scenes_data,
                }
                if depth != "framework":
                    ch_entry["goal_preview"] = (ch.goal or "")[:100]
                if depth == "summary":
                    ch_entry["goal"] = ch.goal or ""
                    ch_entry["status"] = ch.status or ""
                chapters_data.append(ch_entry)

            acts_data.append({
                "id": str(act.id),
                "name": act.name,
                "sort_order": act.sort_order,
                "chapters": chapters_data,
            })

        chars_result = await self.db.execute(
            select(Character).where(Character.project_id == project_id).order_by(Character.sort_order)
        )
        characters_data = []
        for c in chars_result.scalars().all():
            entry: dict[str, Any] = {
                "id": str(c.id),
                "name": c.name,
                "role": c.role or "",
            }
            if depth == "framework":
                # Framework = 名单即可：渲染只显示名称/类型/ID。全档案文本
                # 由 read_character 按需读取，不在每轮缓存中搬运。
                characters_data.append(entry)
                continue
            if depth in ("summary", "full"):
                entry["personality"] = (c.personality or "")[:200]
            characters_data.append(entry)

        available_skills = await self._get_available_skills()

        # Relations and edges — now included at all depths
        rels_result = await self.db.execute(
            select(CharacterRelation).where(CharacterRelation.project_id == project_id)
        )
        relations_data = [row_to_dict(r) for r in rels_result.scalars().all()]
        relations_data = await self._decorate_relations(relations_data, project_id)

        edges_result = await self.db.execute(
            select(ChapterEdge).where(ChapterEdge.project_id == project_id)
        )
        edges_data = [row_to_dict(e) for e in edges_result.scalars().all()]
        edges_data = await self._decorate_edges(edges_data, project_id)

        scene_count = sum(len(scenes_by_chapter.get(cid, [])) for cid in chapter_ids)

        proj_global_settings = proj.global_settings or ""
        gs_len = len(proj_global_settings)

        result = {
            "project": {
                "id": str(proj.id),
                "title": proj.title,
                "genre": proj.genre or "",
                "logline": proj.logline or "",
                "status": proj.status or "",
                "global_settings": proj_global_settings[:2000],
                "global_settings_chars": gs_len,
            },
            "acts": acts_data,
            "characters": characters_data,
            "relations": relations_data,
            "edges": edges_data,
            "available_skills": available_skills,
            "chapter_count": len(all_chapters),
            "scene_count": scene_count,
        }

        note = self._truncation_note(tree)
        if note:
            result["truncated_note"] = note

        # RAG is query-dependent and only consumed by the final response pass —
        # never stored in the shared per-project cache (it would pollute other
        # sessions with one session's query hint).  Consumers call
        # ``get_rag_context`` on demand.
        await self._cache_set(ck, result)

        return result

    async def get_rag_context(self, project_id: uuid.UUID, query_hint: str = "") -> str:
        """Fetch RAG reference knowledge on demand (final response pass only).

        Kept out of ``build_summary``: per-turn builds must not pay a vector
        retrieval that nothing in the tool loop consumes, and caching it would
        leak one session's query into every other session's snapshot.
        """
        proj = await self._get_project(project_id)
        genre = (proj.genre or "") if proj else ""
        rag = await self._get_rag_context_if_meaningful(query_hint, genre)
        return rag or ""

    # ------------------------------------------------------------------
    # build_for_writing — focused context for the WritingAgent
    # ------------------------------------------------------------------

    async def build_for_writing(self, scene_id: uuid.UUID, action: str = "write") -> dict:
        """Build a focused context dict for WritingAgent.

        Returns only what a writing agent needs — no tool definitions,
        no safety rules, no session state. Includes the scene, its POV
        character, related edges, and continuity context.
        """
        ctx: dict[str, Any] = {}

        # 1. Scene
        result = await self.db.execute(select(Scene).where(Scene.id == scene_id))
        scene = result.scalar_one_or_none()
        if not scene:
            return ctx

        ctx["scene_title"] = scene.title or ""
        ctx["scene_summary"] = scene.summary or ""
        ctx["scene_setting"] = scene.setting or ""
        ctx["scene_time"] = scene.scene_time or ""
        ctx["pov_character_name"] = scene.pov_character or ""

        # 2. Scene content (existing)
        result = await self.db.execute(
            select(SceneContent).where(SceneContent.scene_id == scene_id)
        )
        sc = result.scalar_one_or_none()
        existing_content = sc.content or "" if sc else ""
        if existing_content:
            if action in ("continue", "rewrite"):
                ctx["existing_content_tail"] = existing_content[-1500:]
            ctx["existing_content"] = existing_content

        # 3. Chapter
        result = await self.db.execute(
            select(Chapter).where(Chapter.id == scene.chapter_id)
        )
        chapter = result.scalar_one_or_none()
        if chapter:
            ctx["chapter_title"] = chapter.title or ""
            ctx["chapter_sort_order"] = chapter.sort_order
            ctx["chapter_goal"] = chapter.goal or ""

            # 4. Act
            if chapter.act_id:
                result = await self.db.execute(
                    select(Act).where(Act.id == chapter.act_id)
                )
                act = result.scalar_one_or_none()
                ctx["act_name"] = act.name if act else ""
            else:
                ctx["act_name"] = ""

            # 5. Previous scene (continuity)
            result = await self.db.execute(
                select(Scene)
                .where(
                    Scene.chapter_id == chapter.id,
                    Scene.sort_order < scene.sort_order,
                )
                .order_by(Scene.sort_order.desc())
                .limit(1)
            )
            prev_scene = result.scalar_one_or_none()
            if prev_scene:
                result = await self.db.execute(
                    select(SceneContent).where(SceneContent.scene_id == prev_scene.id)
                )
                prev_content = result.scalar_one_or_none()
                if prev_content and prev_content.content:
                    ctx["previous_scene_tail"] = prev_content.content[-500:]

            # 6. Chapter scenes framework
            result = await self.db.execute(
                select(Scene)
                .where(Scene.chapter_id == chapter.id)
                .order_by(Scene.sort_order)
            )
            chapter_scene_list = result.scalars().all()
            if len(chapter_scene_list) > 1:
                ch_scene_ids = [s.id for s in chapter_scene_list]
                result = await self.db.execute(
                    select(SceneContent).where(SceneContent.scene_id.in_(ch_scene_ids))
                )
                content_status = {
                    sc.scene_id: bool(sc.content and sc.content.strip())
                    for sc in result.scalars().all()
                }

                current_idx = None
                for i, s in enumerate(chapter_scene_list):
                    if s.id == scene_id:
                        current_idx = i
                        break

                framework_lines = []
                for i, s in enumerate(chapter_scene_list):
                    title = s.title or "未命名场景"
                    pov = s.pov_character or ""

                    markers = []
                    is_current = s.id == scene_id
                    if is_current:
                        markers.append("← 当前场景")
                    elif current_idx is not None and i == current_idx + 1:
                        markers.append("→ 下一场")

                    has = content_status.get(s.id, False)
                    if has:
                        markers.append("✅ 已有正文")
                    elif not is_current:
                        markers.append("⬜ 待写入")

                    marker_str = f"（{' '.join(markers)}）" if markers else ""

                    line = f"- **{s.sort_order}. {title}**"
                    if pov:
                        line += f" — POV: {pov}"
                    if marker_str:
                        line += f" {marker_str}"

                    summary = (s.summary or "")[:200]
                    if summary:
                        line += f"\n  {summary}"

                    framework_lines.append(line)

                ctx["chapter_scenes_framework"] = "\n".join(framework_lines)

            # 7. Related edges for this chapter
            result = await self.db.execute(
                select(ChapterEdge).where(
                    ChapterEdge.project_id == scene.project_id,
                    ChapterEdge.source_id == chapter.id,
                )
            )
            edges = result.scalars().all()
            if edges:
                # Fetch chapter titles for edge display
                ch_ids = set()
                for e in edges:
                    ch_ids.add(e.source_id)
                    ch_ids.add(e.target_id)
                ch_result = await self.db.execute(
                    select(Chapter).where(Chapter.id.in_(list(ch_ids)))
                )
                ch_map = {ch.id: ch.title for ch in ch_result.scalars().all()}
                edge_lines = []
                for e in edges:
                    src = ch_map.get(e.source_id, "?")
                    tgt = ch_map.get(e.target_id, "?")
                    edge_lines.append(f"- {e.edge_type}: {src} → {tgt}")
                ctx["related_edges"] = "\n".join(edge_lines)

        # 9. Project info
        result = await self.db.execute(
            select(Project).where(Project.id == scene.project_id)
        )
        proj = result.scalar_one_or_none()
        if proj:
            ctx["project_title"] = proj.title or ""
            ctx["genre"] = proj.genre or ""
            ctx["global_settings"] = (proj.global_settings or "")[:2000]

        # 10. POV character detail
        if scene.pov_character:
            result = await self.db.execute(
                select(Character).where(
                    Character.project_id == scene.project_id,
                    Character.name == scene.pov_character,
                )
            )
            pov = result.scalar_one_or_none()
            if pov:
                parts = [f"## {pov.name}（{pov.role or '角色'}）"]
                if pov.personality:
                    parts.append(f"性格：{pov.personality}")
                if pov.motivation:
                    parts.append(f"动机：{pov.motivation}")
                if pov.background:
                    parts.append(f"背景：{pov.background}")
                if pov.appearance:
                    parts.append(f"外貌：{pov.appearance}")
                ctx["pov_character_detail"] = "\n".join(parts)

        # 11. Other characters (all project characters, excluding POV)
        result = await self.db.execute(
            select(Character)
            .where(Character.project_id == scene.project_id)
            .order_by(Character.sort_order)
        )
        all_chars = result.scalars().all()
        other_lines = []
        total = 0
        limit = 3000
        for c in all_chars:
            if scene.pov_character and c.name == scene.pov_character:
                continue
            parts = [f"- {c.name}（{c.role or '角色'}）"]
            if c.personality:
                parts.append(f"  性格：{c.personality[:100]}")
            if c.motivation:
                parts.append(f"  动机：{c.motivation[:100]}")
            block = "\n".join(parts)
            total += len(block) + 1
            if total > limit and other_lines:
                other_lines.append(f"  （还有 {len(all_chars) - len(other_lines)} 个角色略）")
                break
            other_lines.append(block)
        if other_lines:
            ctx["other_characters"] = "\n".join(other_lines)

        ctx["available_skills"] = await self._get_available_skills()
        return ctx

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_project(self, project_id: uuid.UUID):
        r = await self.db.execute(select(Project).where(Project.id == project_id))
        return r.scalar_one_or_none()

    async def _get_config(self, project_id: uuid.UUID):
        r = await self.db.execute(select(ProjectConfig).where(ProjectConfig.project_id == project_id))
        return r.scalar_one_or_none()

    async def _characters_text(self, project_id: uuid.UUID) -> str:
        r = await self.db.execute(
            select(Character).where(Character.project_id == project_id).order_by(Character.sort_order)
        )
        chars = r.scalars().all()
        if not chars:
            return "暂无角色"
        total_chars_limit = 4000
        total = 0
        lines = []
        for c in chars:
            parts = [f"- {c.name}（{c.role or '未指定角色'}）"]
            if c.personality:
                parts.append(f"  性格：{c.personality[:200]}")
            if c.motivation:
                parts.append(f"  动机：{c.motivation[:200]}")
            if c.background:
                parts.append(f"  背景：{c.background[:300]}")
            block = "\n".join(parts)
            total += len(block) + 1
            if total > total_chars_limit:
                lines.append(f"  （还有 {len(chars) - len(lines)} 个角色略）")
                break
            lines.append(block)
        return "\n".join(lines)

    async def _relations_text(self, project_id: uuid.UUID) -> str:
        r = await self.db.execute(
            select(CharacterRelation).where(CharacterRelation.project_id == project_id)
        )
        rels = r.scalars().all()
        if not rels:
            return "暂无关系"
        char_map = {}
        cr = await self.db.execute(select(Character).where(Character.project_id == project_id))
        for c in cr.scalars().all():
            char_map[c.id] = c.name
        lines = []
        for rel in rels:
            src = char_map.get(rel.character_id, "?")
            tgt = char_map.get(rel.target_id, "?")
            trust = ""
            if rel.trust and rel.trust != 50:
                trust = f" (信任{rel.trust})"
            lines.append(f"- {src} → {rel.label or rel.rel_type or '关联'} → {tgt}{trust}")
        return "\n".join(lines)

    async def _get_available_skills(self) -> list:
        try:
            return await self.skill_engine.get_all_skills_meta()
        except Exception:
            logger.warning("Failed to load skills", exc_info=True)
            return []

    async def _decorate_relations(self, relations_data: list[dict], project_id: uuid.UUID) -> list[dict]:
        """Resolve character ids to names so relation rows are usable by the LLM."""
        if not relations_data:
            return relations_data
        char_ids: set[str] = set()
        for r in relations_data:
            if r.get("character_id"):
                char_ids.add(str(r["character_id"]))
            if r.get("target_id"):
                char_ids.add(str(r["target_id"]))
        parsed = []
        for c in char_ids:
            try:
                parsed.append(uuid.UUID(c))
            except (ValueError, TypeError):
                continue
        names: dict[str, str] = {}
        if parsed:
            result = await self.db.execute(
                select(Character.id, Character.name).where(Character.id.in_(parsed))
            )
            names = {str(k): v for k, v in result.all()}
        for r in relations_data:
            r["character_name"] = names.get(str(r.get("character_id")), "") or str(r.get("character_id", ""))
            r["target_name"] = names.get(str(r.get("target_id")), "") or str(r.get("target_id", ""))
        return relations_data

    async def _decorate_edges(self, edges_data: list[dict], project_id: uuid.UUID) -> list[dict]:
        """Resolve chapter ids to titles so edge rows are usable by the LLM."""
        if not edges_data:
            return edges_data
        ch_ids: set[str] = set()
        for e in edges_data:
            if e.get("source_id"):
                ch_ids.add(str(e["source_id"]))
            if e.get("target_id"):
                ch_ids.add(str(e["target_id"]))
        parsed = []
        for c in ch_ids:
            try:
                parsed.append(uuid.UUID(c))
            except (ValueError, TypeError):
                continue
        titles: dict[str, str] = {}
        if parsed:
            result = await self.db.execute(
                select(Chapter.id, Chapter.title).where(Chapter.id.in_(parsed))
            )
            titles = {str(k): v for k, v in result.all()}
        for e in edges_data:
            e["source_title"] = titles.get(str(e.get("source_id")), "") or str(e.get("source_id", ""))
            e["target_title"] = titles.get(str(e.get("target_id")), "") or str(e.get("target_id", ""))
        return edges_data

    async def _get_rag_context_if_meaningful(self, query_hint: str, genre: str) -> str:
        if not _is_meaningful_query(query_hint):
            return ""
        rag_query = query_hint[:200] if query_hint else f"{genre} 创作指南 写作技巧"
        try:
            return await self.rag_engine.retrieve_context(
                project_id=None,
                genre=genre or None,
                query=rag_query,
            )
        except Exception as e:
            logger.warning("RAG context retrieval failed: %s", e, exc_info=True)
            return ""
