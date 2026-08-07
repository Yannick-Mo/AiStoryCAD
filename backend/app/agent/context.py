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
    Theme,
    ThemeChapter,
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
        ``read_full_project``) don't serve a stale skeleton from the shared
        300s cache within the same turn.
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
    # Shared project tree loader (used by both build_full and build_summary)
    # ------------------------------------------------------------------

    async def _load_project_tree(self, project_id: uuid.UUID, limit_chapters: int = 200, limit_scenes: int = 1000) -> dict:
        acts_result = await self.db.execute(
            select(Act).where(Act.project_id == project_id).order_by(Act.sort_order)
        )
        acts = acts_result.scalars().all()

        chapter_total_result = await self.db.execute(
            select(func.count()).select_from(Chapter).where(Chapter.project_id == project_id)
        )
        chapter_total = chapter_total_result.scalar_one() or 0
        chapters_result = await self.db.execute(
            select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.sort_order).limit(limit_chapters)
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
            select(Scene).where(Scene.chapter_id.in_(chapter_ids)).order_by(Scene.sort_order).limit(limit_scenes)
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
    # Build full
    # ------------------------------------------------------------------

    async def build_full(self, project_id: uuid.UUID, query_hint: str = "") -> dict:
        ck = self._cache_key(project_id, "full", "")
        cached = await self._cache_get(ck)
        if cached is not None:
            # RAG is query-dependent — never serve it stale from the framework
            # cache; refresh it fresh so cache hits still get relevant context.
            result = dict(cached)
            genre = (result.get("project") or {}).get("genre") or ""
            rag_context = await self._get_rag_context_if_meaningful(query_hint, genre)
            result["rag_context"] = rag_context or ""
            return result

        proj = await self._get_project(project_id)
        if not proj:
            return {}

        config = await self._get_config(project_id)
        tree = await self._load_project_tree(project_id)
        acts = tree["acts"]
        all_chapters = tree["all_chapters"]
        chapters_by_act = tree["chapters_by_act"]
        all_scenes = tree["all_scenes"]
        scenes_by_chapter = tree["scenes_by_chapter"]
        chapter_ids = tree["chapter_ids"]

        scene_ids = [sc.id for sc in all_scenes]
        # Blueprints (Scene.summary) are the canonical story memory — the full
        # context never carries body text.  Only a cheap "has body?" flag so
        # the model knows a scene was written without reading it.
        written_by_scene: dict[uuid.UUID, bool] = {}
        if scene_ids:
            sc_content_result = await self.db.execute(
                select(SceneContent.scene_id).where(
                    SceneContent.scene_id.in_(scene_ids),
                    SceneContent.content.isnot(None),
                    SceneContent.content != "",
                )
            )
            written_by_scene = {sc_id: True for (sc_id,) in sc_content_result.all()}

        themes_by_chapter: dict[uuid.UUID, list[str]] = defaultdict(list)
        tc_result = await self.db.execute(
            select(ThemeChapter.chapter_id, Theme.name)
            .join(Theme, Theme.id == ThemeChapter.theme_id)
            .where(Theme.project_id == project_id)
        )
        for ch_id, theme_name in tc_result.all():
            themes_by_chapter[ch_id].append(theme_name)

        acts_data = []
        for act in acts:
            chapters_data = []
            for ch in chapters_by_act.get(act.id, []):
                scenes_data = []
                for sc in scenes_by_chapter.get(ch.id, []):
                    sc_d = row_to_dict(sc)
                    sc_d["written"] = written_by_scene.get(sc.id, False)
                    scenes_data.append(sc_d)
                ch_d = row_to_dict(ch)
                ch_d["scenes"] = scenes_data
                ch_d["themes"] = themes_by_chapter.get(ch.id, [])
                chapters_data.append(ch_d)
            act_d = row_to_dict(act)
            act_d["chapters"] = chapters_data
            acts_data.append(act_d)

        chars_result = await self.db.execute(
            select(Character).where(Character.project_id == project_id).order_by(Character.sort_order)
        )
        characters_data = [row_to_dict(c) for c in chars_result.scalars().all()]

        rels_result = await self.db.execute(
            select(CharacterRelation).where(CharacterRelation.project_id == project_id)
        )
        relations_data = [row_to_dict(r) for r in rels_result.scalars().all()]
        relations_data = await self._decorate_relations(relations_data, project_id)

        themes_result = await self.db.execute(
            select(Theme).where(Theme.project_id == project_id).order_by(Theme.sort_order)
        )
        themes_data = [row_to_dict(t) for t in themes_result.scalars().all()]

        edges_result = await self.db.execute(
            select(ChapterEdge).where(ChapterEdge.project_id == project_id)
        )
        edges_data = [row_to_dict(e) for e in edges_result.scalars().all()]
        edges_data = await self._decorate_edges(edges_data, project_id)

        available_skills = await self._get_available_skills()

        rag_context = await self._get_rag_context_if_meaningful(query_hint, proj.genre or "")

        result = {
            "project": row_to_dict(proj),
            "config": row_to_dict(config) if config else {},
            "acts": acts_data,
            "characters": characters_data,
            "relations": relations_data,
            "themes": themes_data,
            "edges": edges_data,
            "available_skills": available_skills,
            "rag_context": rag_context or "",
        }
        note = self._truncation_note(tree)
        if note:
            result["truncated_note"] = note

        await self._cache_set(ck, result)
        return result

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
                result = dict(cached)
                genre = (result.get("project") or {}).get("genre") or ""
                rag_context = await self._get_rag_context_if_meaningful(query_hint, genre)
                result["rag_context"] = rag_context or ""
                return result

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
                    entry: dict[str, Any] = {
                        "id": str(sc.id),
                        "title": sc.title,
                        "sort_order": sc.sort_order,
                        "summary": (sc.summary or "")[:500],
                        "pov_character": sc.pov_character or "",
                    }
                    if depth in ("summary", "framework"):
                        entry["setting"] = sc.setting or ""
                        entry["scene_time"] = sc.scene_time or ""
                        entry["summary"] = (sc.summary or "")[:1000]
                    scenes_data.append(entry)

                ch_entry: dict[str, Any] = {
                    "id": str(ch.id),
                    "title": ch.title,
                    "sort_order": ch.sort_order,
                    "goal_preview": (ch.goal or "")[:100],
                    "scenes": scenes_data,
                }
                if depth in ("summary", "full", "framework"):
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
            if depth in ("summary", "full"):
                entry["personality"] = (c.personality or "")[:200]
            if depth == "framework":
                entry["personality"] = c.personality or ""
                entry["motivation"] = c.motivation or ""
                entry["background"] = c.background or ""
                entry["appearance"] = c.appearance or ""
                entry["arc"] = c.arc or ""
            characters_data.append(entry)

        themes_result = await self.db.execute(
            select(Theme).where(Theme.project_id == project_id).order_by(Theme.sort_order)
        )
        themes_data = []
        for t in themes_result.scalars().all():
            entry = {"name": t.name, "proposition": t.proposition or ""}
            if depth == "framework":
                entry["note"] = t.note or ""
            themes_data.append(entry)

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
        if depth == "framework":
            proj_global_settings = proj_global_settings

        result = {
            "project": {
                "id": str(proj.id),
                "title": proj.title,
                "genre": proj.genre or "",
                "logline": proj.logline or "",
                "status": proj.status or "",
                "global_settings": proj_global_settings if depth == "framework" else proj_global_settings[:2000],
            },
            "acts": acts_data,
            "characters": characters_data,
            "themes": themes_data,
            "relations": relations_data,
            "edges": edges_data,
            "available_skills": available_skills,
            "chapter_count": len(all_chapters),
            "scene_count": scene_count,
        }

        note = self._truncation_note(tree)
        if note:
            result["truncated_note"] = note

        # RAG is query-dependent — compute fresh, but keep the cached dict
        # self-contained for other consumers.
        rag_context = await self._get_rag_context_if_meaningful(query_hint, proj.genre or "")
        result["rag_context"] = rag_context or ""

        await self._cache_set(ck, result)

        return result

    # ------------------------------------------------------------------
    # build_for_writing — focused context for the WritingAgent
    # ------------------------------------------------------------------

    async def build_for_writing(self, scene_id: uuid.UUID, action: str = "write") -> dict:
        """Build a focused context dict for WritingAgent.

        Returns only what a writing agent needs — no tool definitions,
        no safety rules, no session state. Includes the scene, its POV
        character, related themes/edges, and continuity context.
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

            # 8. Related themes (via ThemeChapter)
            result = await self.db.execute(
                select(ThemeChapter).where(ThemeChapter.chapter_id == chapter.id)
            )
            tc_links = result.scalars().all()
            if tc_links:
                theme_ids = [t.theme_id for t in tc_links]
                result = await self.db.execute(
                    select(Theme).where(Theme.id.in_(theme_ids))
                )
                themes = result.scalars().all()
                theme_lines = []
                for t in themes:
                    prop = f" — {t.proposition}" if t.proposition else ""
                    theme_lines.append(f"- {t.name}{prop}")
                ctx["related_themes"] = "\n".join(theme_lines)

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

    async def _themes_text(self, project_id: uuid.UUID) -> str:
        r = await self.db.execute(
            select(Theme).where(Theme.project_id == project_id).order_by(Theme.sort_order)
        )
        themes = r.scalars().all()
        if not themes:
            return "暂无主题"
        lines = []
        for t in themes:
            prop = f" — {t.proposition}" if t.proposition else ""
            lines.append(f"- {t.name}{prop}")
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
