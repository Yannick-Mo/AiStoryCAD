from __future__ import annotations

import json
import logging
import time
import uuid
from collections import OrderedDict
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.rag import RAGEngine
from app.knowledge.skill_engine import _shared_engine as _shared_skill_engine
from app.project.models import Project
from app.storycad.order import order_by_sequence, tie_break
from app.storycad.repository import with_chapter_content
from app.storycad.sequence import ordered_chapter_ids
from app.storycad.models import (
    Act,
    Chapter,
    Character,
    CharacterRelation,
    Setup,
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


# ── 交给写手的材料预算 ──────────────────────────────────────────────────────
# 写手这一次输入的体量只有一万多 token（模型窗口的百分之几）。为了省这点
# token 把世界观截到 2000 字、把人物性格硬切到 200 字，换不来任何东西，代价
# 却是写手看不见完整规则与人物——于是它自己发明设定，下一章又和上一章对不上。
_GLOBAL_SETTINGS_BUDGET = 8000
_PREV_TAIL_CHARS = 800
_CHARACTERS_BUDGET = 8000
# 人物档案分给写手的份额。**外貌必须在场**：先前只给性格/动机/弧光/背景，结果每一章的
# 长相都是写手现编的——豆彪的鸳鸯脸、柳如烟的月白道袍与素色长剑、赵怀瑾的折扇，
# 全书一次都没进过正文。
_CHAR_FIELD_LIMITS = {
    "appearance": 200, "personality": 320, "motivation": 320, "arc": 320, "background": 480,
}
_ROLE_LABELS = {"main": "主角", "supporting": "配角", "minor": "次要"}

# 篇幅参考：本书自己的手感是最准的尺子；还没有正文时用单章常规体量。
_LENGTH_FALLBACK = (2500, 4000)
_LENGTH_MIN_WRITTEN = 500


def _clip_text(text: str, limit: int) -> str:
    """按句末标点截断，并留下省略标记。

    历史上这里是 ``personality[:200]``：写手读到的是「……骨子里有」这种断在
    半句话上的档案。不完整的句子比少一句话更糟——模型会自己去补完它，而补出来
    的东西没人审。宁可在最后一个句末标点处少给一句，并明确标出「后文略」。
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    for punct in ("。", "！", "？", "；", "…"):
        cut = window.rfind(punct)
        if cut >= limit * 0.6:
            return window[:cut + 1] + "……（后文略）"
    return window + "……（后文略）"


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
        ``read_chapters`` / ``read_chapter_package``) don't serve a stale skeleton from
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
    ) -> dict:
        """框架树：幕 + 章（**不含正文**）+ 每章正文字符数。

        正文列（``chapters.content``）是 deferred 的，所以即使章节很多，这里也
        不会把全文拖进内存——结构性保证，不是靠自觉不写。要知道"哪些章写了正文"
        只需单独取一次长度，不必读正文。
        """
        acts = (await self.db.execute(
            select(Act).where(Act.project_id == project_id).order_by(*order_by_sequence(Act))
        )).scalars().all()

        chapter_total = (await self.db.execute(
            select(func.count()).select_from(Chapter).where(Chapter.project_id == project_id)
        )).scalar_one() or 0
        all_chapters = (await self.db.execute(
            select(Chapter)
            .outerjoin(Act, Act.id == Chapter.act_id)
            .where(Chapter.project_id == project_id)
            .order_by(Act.sort_order.asc(), *order_by_sequence(Chapter))
            .limit(limit_chapters)
        )).scalars().all()
        chapter_truncated = chapter_total > len(all_chapters)

        chapters_by_act: dict[uuid.UUID, list] = {}
        for chapter in all_chapters:
            chapters_by_act.setdefault(chapter.act_id, []).append(chapter)

        body_rows = (await self.db.execute(
            select(Chapter.id, func.length(func.coalesce(Chapter.content, "")).label("body_chars"))
            .where(Chapter.project_id == project_id)
        )).all()
        body_chars = {cid: int(n or 0) for cid, n in body_rows}

        return {
            "acts": acts,
            "all_chapters": all_chapters,
            "chapters_by_act": chapters_by_act,
            "body_chars": body_chars,
            "chapter_total": chapter_total,
            "chapter_truncated": chapter_truncated,
        }

    @staticmethod
    def _truncation_note(tree: dict) -> str:
        """框架树被上限截断时的诚实标记。"""
        if not tree.get("chapter_truncated"):
            return ""
        return (
            f"（项目规模较大：共 {tree.get('chapter_total', '?')} 章，"
            f"本次仅列出前 {len(tree.get('all_chapters', []))} 章，其余未在列表中显示）"
        )

    async def build_chapter_package(
        self, project_id: uuid.UUID, chapter_id: uuid.UUID
    ) -> dict | None:
        """动一章之前要的全部输入（规格第 8 节：把七步里的第 1、3、4 步合成一次调用）。

        包含：

        1. 这一章：要写什么（goal）、写成了什么（result）、状态、全局章号、所属幕；
        2. 上一章的结果——这就是这一章的起点（第 1 步）；
        3. 它在整本书里的位置：幕的 purpose + 项目的 终局 / 主线骨架 / 一句话梗概（第 3 步）；
        4. 人物：每个人的「他想要什么」+「他从哪到哪」（第 4 步）；
        5. 还没回收的伏笔（第 6 步的预读）。

        正文只给长度与尾部片段：要判断"接着写还是重写"够了，不必把全文搬进来
        （要看全文用 ``read_chapter_content``）。
        ``None`` 表示这一章不在这个项目里。
        """
        chapter = (await self.db.execute(
            select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
        )).scalar_one_or_none()
        if not chapter:
            return None

        # 正文只取"长度 + 尾部片段"，不把全文拖进来。用列查询而不是读属性：
        # content 是 deferred 列，直接读会在 async 会话里抛 MissingGreenlet。
        body_length, body_tail = (await self.db.execute(
            select(
                func.length(func.coalesce(Chapter.content, "")),
                func.right(func.coalesce(Chapter.content, ""), 500),
            ).where(Chapter.id == chapter.id)
        )).one()
        body_length = int(body_length or 0)
        body_tail = body_tail or ""

        act = (await self.db.execute(
            select(Act).where(Act.id == chapter.act_id)
        )).scalar_one_or_none()
        project = await self._get_project(project_id)

        ordered = await ordered_chapter_ids(self.db, project_id)
        try:
            global_order = ordered.index(chapter.id) + 1
        except ValueError:
            global_order = None

        def _brief(row: Chapter | None) -> dict | None:
            if row is None:
                return None
            return {
                "id": str(row.id),
                "title": row.title or "",
                "goal": row.goal or "",
                "result": row.result or "",
                "status": row.status or "",
            }

        prev_chapter = next_chapter = None
        if global_order:
            if global_order > 1:
                prev_chapter = (await self.db.execute(
                    select(Chapter).where(Chapter.id == ordered[global_order - 2])
                )).scalar_one_or_none()
            if global_order < len(ordered):
                next_chapter = (await self.db.execute(
                    select(Chapter).where(Chapter.id == ordered[global_order])
                )).scalar_one_or_none()

        chars = (await self.db.execute(
            select(Character).where(Character.project_id == project_id)
            .order_by(*order_by_sequence(Character))
        )).scalars().all()

        setups = (await self.db.execute(
            select(Setup).where(Setup.project_id == project_id, Setup.status == "open")
            .order_by(*order_by_sequence(Setup))
        )).scalars().all()
        order_map = {cid: index + 1 for index, cid in enumerate(ordered)}

        return {
            "chapter": {
                "id": str(chapter.id),
                "title": chapter.title or "",
                "goal": chapter.goal or "",
                "result": chapter.result or "",
                "status": chapter.status or "",
                "sort_order": chapter.sort_order,
                "global_order": global_order,
                "body_chars": body_length,
                "body_tail": body_tail if body_length else "",
            },
            "act": {
                "id": str(act.id) if act else "",
                "name": act.name if act else "",
                "purpose": (act.purpose or "") if act else "",
            } if act else None,
            "project": {
                "title": project.title if project else "",
                "genre": project.genre if project else "",
                "logline": (project.logline or "") if project else "",
                "spine": (project.spine or "") if project else "",
                "ending": (project.ending or "") if project else "",
                "total_words": int(project.total_words or 0) if project else 0,
                "global_settings": (project.global_settings or "") if project else "",
            },
            "prev_chapter": _brief(prev_chapter),
            "next_chapter": _brief(next_chapter),
            "characters": [
                {
                    "id": str(c.id),
                    "name": c.name or "",
                    "role": c.role or "",
                    "personality": c.personality or "",
                    "motivation": c.motivation or "",
                    "arc": c.arc or "",
                    "background": c.background or "",
                }
                for c in chars
            ],
            "open_setups": [
                {
                    "id": str(s.id),
                    "note": s.note or "",
                    "planted_in_order": order_map.get(s.planted_in),
                    "pay_off_in_order": order_map.get(s.pay_off_in) if s.pay_off_in else None,
                    "pay_off_in_planned": bool(s.pay_off_in),
                }
                for s in setups
            ],
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
                     .order_by(Act.sort_order.desc(), *tie_break(Act, True)).limit(1))
        else:
            act_q = (select(Act).where(Act.project_id == project_id,
                                       Act.sort_order > (act.sort_order or 0))
                     .order_by(Act.sort_order.asc(), *tie_break(Act)).limit(1))
        other = (await self.db.execute(act_q)).scalar_one_or_none()
        if not other:
            return None
        if direction < 0:
            ch_q = (select(Chapter).where(Chapter.project_id == project_id,
                                          Chapter.act_id == other.id)
                    .order_by(Chapter.sort_order.desc(), *tie_break(Chapter, True)).limit(1))
        else:
            ch_q = (select(Chapter).where(Chapter.project_id == project_id,
                                          Chapter.act_id == other.id)
                    .order_by(Chapter.sort_order.asc(), *tie_break(Chapter)).limit(1))
        nbr2 = (await self.db.execute(ch_q)).scalar_one_or_none()
        if not nbr2:
            return None
        return {"title": nbr2.title or "", "goal": (nbr2.goal or "")[:800],
                "sort_order": nbr2.sort_order}

    async def build_progress(self, project_id: uuid.UUID) -> dict:
        """进度：**算出来的清单，不是表**（规格第 4.7 节）。

        内容：设计缺什么 · 正文断点 · 伏笔逾期 · 定稿进度 · 最近改动的章 · 下一步。

        它同时取代了三个旧工具：``suggest_next``（原来因为一个无效的数据库构造
        必定报错）、``project_health``、以及"沿场遍历找断点"的逻辑。
        """
        acts = (await self.db.execute(
            select(Act).where(Act.project_id == project_id).order_by(*order_by_sequence(Act))
        )).scalars().all()

        rows = (await self.db.execute(
            select(
                Chapter.id,
                Chapter.act_id,
                Chapter.title,
                Chapter.goal,
                Chapter.result,
                Chapter.status,
                Chapter.sort_order,
                Chapter.updated_at,
                Act.name.label("act_name"),
                Act.sort_order.label("act_order"),
                Act.purpose.label("act_purpose"),
                func.length(func.coalesce(Chapter.content, "")).label("body_chars"),
            )
            .join(Act, Act.id == Chapter.act_id)
            .where(Chapter.project_id == project_id)
            .order_by(Act.sort_order.asc(), Chapter.sort_order.asc(), *tie_break(Chapter))
        )).all()

        ordered = list(rows)
        global_order = {row.id: index + 1 for index, row in enumerate(ordered)}
        written_orders = {global_order[row.id] for row in ordered if (row.body_chars or 0) > 0}

        # ── 设计缺什么 ──
        design_gaps: list[dict[str, Any]] = []
        for act in acts:
            if not (act.purpose or "").strip():
                design_gaps.append({
                    "what": "幕没写「这一幕要干什么」",
                    "act_id": str(act.id),
                    "act_name": act.name or "",
                    "fix_with": "update_act",
                })
        for row in ordered:
            if not (row.goal or "").strip():
                design_gaps.append({
                    "what": "章没写「本章要写什么」",
                    "chapter_id": str(row.id),
                    "global_order": global_order[row.id],
                    "title": row.title or "",
                    "fix_with": "update_chapter",
                })
            elif (row.body_chars or 0) > 0 and not (row.result or "").strip():
                design_gaps.append({
                    "what": "章有正文但没写「本章写成了什么」——下一章没有起点",
                    "chapter_id": str(row.id),
                    "global_order": global_order[row.id],
                    "title": row.title or "",
                    "fix_with": "update_chapter",
                })
        chars = (await self.db.execute(
            select(Character).where(Character.project_id == project_id)
            .order_by(*order_by_sequence(Character))
        )).scalars().all()
        for char in chars:
            if not (char.motivation or "").strip():
                design_gaps.append({
                    "what": "人物没写「他想要什么」",
                    "character_id": str(char.id),
                    "name": char.name or "",
                    "fix_with": "update_character",
                })

        # ── 正文断点：第一个正文为空的章 ──
        first_unwritten = next((row for row in ordered if (row.body_chars or 0) == 0), None)
        content_break = None
        if first_unwritten is not None:
            content_break = {
                "global_order": global_order[first_unwritten.id],
                "chapter_id": str(first_unwritten.id),
                "title": first_unwritten.title or "",
                "act_name": first_unwritten.act_name or "",
            }

        # ── 伏笔逾期：打算在哪一章收，那一章已经写了，状态却还是 open ──
        setups = (await self.db.execute(
            select(Setup).where(Setup.project_id == project_id)
        )).scalars().all()
        overdue_setups: list[dict[str, Any]] = []
        open_setups: list[dict[str, Any]] = []
        for setup in setups:
            entry = {
                "id": str(setup.id),
                "note": setup.note or "",
                "status": setup.status or "",
                "planted_in_order": global_order.get(setup.planted_in),
                "pay_off_in_order": global_order.get(setup.pay_off_in) if setup.pay_off_in else None,
            }
            if setup.status == "open":
                open_setups.append(entry)
                target = entry["pay_off_in_order"]
                if target is not None and target in written_orders:
                    overdue_setups.append({**entry, "why": f"第 {target} 章已经写了，伏笔还没收"})

        # ── 定稿进度 ──
        status_counts = {"draft": 0, "revising": 0, "final": 0}
        for row in ordered:
            status_counts[row.status or "draft"] = status_counts.get(row.status or "draft", 0) + 1

        # ── 最近改动的章（按编辑时间倒序，原来由 read_recent_chapters 承担）──
        recent = sorted(
            (row for row in ordered if row.updated_at is not None),
            key=lambda row: row.updated_at,
            reverse=True,
        )[:5]
        recently_changed = [
            {
                # 字段名是 id 而不是 chapter_id：所有容器元素都叫 id，ID 寄存表
                # 就是按 id 收的。这里曾经是 chapter_id，于是「最近改动的章」的
                # ID 永远进不了寄存器。
                "id": str(row.id),
                "global_order": global_order[row.id],
                "title": row.title or "",
                "status": row.status or "",
                "written": (row.body_chars or 0) > 0,
                "updated_at": row.updated_at.isoformat() if hasattr(row.updated_at, "isoformat") else str(row.updated_at),
            }
            for row in recent
        ]

        # ── 下一步：以上清单里最靠前的一条 ──
        if design_gaps:
            next_step = {"kind": "补设计", **design_gaps[0]}
        elif content_break is not None:
            next_step = {"kind": "写下一章", **content_break}
        elif overdue_setups:
            next_step = {"kind": "收伏笔", **overdue_setups[0]}
        else:
            next_step = {"kind": "完成", "note": "设计无缺口、正文无断点、没有逾期伏笔"}

        return {
            "project_id": str(project_id),
            "act_count": len(acts),
            "chapter_count": len(ordered),
            "written_chapter_count": len(written_orders),
            "status_counts": status_counts,
            "design_gaps": design_gaps,
            "content_break": content_break,
            "open_setups": open_setups,
            "overdue_setups": overdue_setups,
            "recently_changed_chapters": recently_changed,
            "next_step": next_step,
        }

    async def build_chapter_window(
        self,
        project_id: uuid.UUID,
        chapter_from: int,
        chapter_to: int,
        budget_chars: int = 20000,
        detail: str = "full",
    ) -> dict:
        """按全局章序号读一个连续范围。

        ``detail``：

        - ``full``（默认）：每章的**要写什么** + **写成了什么** —— 回看前情用这个；
        - ``index``：只给 章号 / 标题 / 状态 / 所属幕 / 有没有正文 —— 粗看长范围用。
          一条 index 行只有 full 的约 1/3 大小，所以同样预算能一次读三倍多的章：
          3000 章的书用 full 一次约 65 章（扫完要 46 次），用 index 约 200 章。

        **两种模式都不给正文**（那是 ``read_chapter_content`` 的事）。

        这是作者写作循环第 1 步的主力（回看上一章成了什么样），也是第 2 步的
        素材（这一章要干什么）。**正文不在里面**——正文由
        ``read_chapter_content`` 按需读取。

        输出在这里按预算裁剪，而不是交给执行器 8000 字符的天花板，这样
        ``truncated`` / ``next_from`` 两个结构标记才不会先被截掉。
        """
        index_mode = detail == "index"
        ordered_ids = await ordered_chapter_ids(self.db, project_id)
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
                "chapters": [],
                "truncated": False,
                "next_from": None,
            }

        window_ids = ordered_ids[chapter_from - 1: chapter_to]

        rows = (await self.db.execute(
            select(
                Chapter.id,
                Chapter.title,
                Chapter.goal,
                Chapter.result,
                Chapter.status,
                Chapter.sort_order,
                Chapter.act_id,
                Act.name.label("act_name"),
                func.length(func.coalesce(Chapter.content, "")).label("body_chars"),
            )
            .join(Act, Act.id == Chapter.act_id)
            .where(Chapter.id.in_(window_ids))
        )).all()
        by_id = {row.id: row for row in rows}

        entries: list[dict[str, Any]] = []
        truncated = False
        next_from: int | None = None
        used = 0
        for index, cid in enumerate(window_ids, start=chapter_from):
            row = by_id.get(cid)
            if row is None:
                continue
            if index_mode:
                entry: dict[str, Any] = {
                    "global_order": index,
                    "id": str(row.id),
                    "title": row.title or "",
                    "status": row.status or "",
                    "act_name": row.act_name or "",
                    "written": int(row.body_chars or 0) > 0,
                }
            else:
                entry = {
                    "global_order": index,
                    "id": str(row.id),
                    "title": row.title or "",
                    "sort_order": row.sort_order,
                    "act_id": str(row.act_id) if row.act_id else "",
                    "act_name": row.act_name or "",
                    "status": row.status or "",
                    "goal": row.goal or "",
                    "result": row.result or "",
                    "body_chars": int(row.body_chars or 0),
                }
            est = len(json.dumps(entry, ensure_ascii=False))
            if used + est + 2 > budget_chars:
                truncated = True
                next_from = index
                break
            entries.append(entry)
            used += est + 2

        return {
            "chapter_from": chapter_from,
            "chapter_to": chapter_to,
            "total_chapters": total,
            "detail": detail,
            "chapters": entries,
            "truncated": truncated,
            "next_from": next_from,
        }

    # 常驻地图的窗口半径：以「你正在看的那一章」（没有就看正文断点）为中心，
    # 前后各这么多章进常驻。**覆盖率只与"离你多远"有关，与全书多长无关**——
    # 3000 章的书和 120 章的书，你身边这一圈是一样大的。
    _WINDOW_RADIUS = 120

    async def _build_side_records(
        self, project_id: uuid.UUID, global_order: dict, lean: bool
    ) -> tuple[list, list, list]:
        """三个侧挂记录（人物 / 关系 / 伏笔）。``lean`` 时人物只给名单。"""
        chars_result = await self.db.execute(
            select(Character).where(Character.project_id == project_id)
            .order_by(*order_by_sequence(Character))
        )
        characters_data = []
        for char in chars_result.scalars().all():
            entry: dict[str, Any] = {
                "id": str(char.id), "name": char.name, "role": char.role or "",
            }
            if not lean:
                # framework = 名单即可：全档案文本由 read_character 按需读取。
                entry["motivation"] = (char.motivation or "")[:300]
                entry["arc"] = (char.arc or "")[:300]
                entry["personality"] = (char.personality or "")[:200]
            characters_data.append(entry)

        rels_result = await self.db.execute(
            select(CharacterRelation).where(CharacterRelation.project_id == project_id)
        )
        relations_data = await self._decorate_relations(
            [row_to_dict(r) for r in rels_result.scalars().all()], project_id
        )

        setups_result = await self.db.execute(
            select(Setup).where(Setup.project_id == project_id)
            .order_by(*order_by_sequence(Setup))
        )
        setups_data = await self._decorate_setups(
            [row_to_dict(s) for s in setups_result.scalars().all()], global_order
        )
        return characters_data, relations_data, setups_data

    async def _build_windowed_framework(
        self, proj, focus_chapter_id: uuid.UUID | None = None
    ) -> dict:
        """常驻地图（``depth="framework"`` 走这条路）。

        与 ``summary``/``full`` 的关键区别：**不把全书每一章都建成条目**。
        旧实现给 3000 章建 3000 条详细条目（实测 692KB 的上下文），而渲染器只输出
        其中约 79 条——97% 是每轮白建。这里只取一遍**窄列**
        （id / 幕 / 标题 / 状态 / 正文字数），详细条目只为**窗口内**的章构建。

        窗口中心 = 用户当前在看的那一章（``focus_chapter_id``，来自编辑器的
        ``current_view_id``）；没有就用正文断点（下一章该写的那一章）。
        这样"写到第 2000 章"时，身边那一圈在上下文里，第 20 章不在。
        """
        ordered = await ordered_chapter_ids(self.db, proj.id)
        total = len(ordered)
        order_of = {cid: i + 1 for i, cid in enumerate(ordered)}

        rows = (await self.db.execute(
            select(
                Chapter.id, Chapter.act_id, Chapter.title, Chapter.status,
                func.length(func.coalesce(Chapter.content, "")).label("body_chars"),
            ).where(Chapter.project_id == proj.id)
        )).all()
        meta = {r.id: r for r in rows}

        acts = (await self.db.execute(
            select(Act).where(Act.project_id == proj.id).order_by(*order_by_sequence(Act))
        )).scalars().all()

        per_act: dict[Any, dict[str, Any]] = {}
        written_total = 0
        first_empty: int | None = None
        for r in rows:
            chars = int(r.body_chars or 0)
            if chars > 0:
                written_total += 1
            else:
                g = order_of.get(r.id)
                if g is not None and (first_empty is None or g < first_empty):
                    first_empty = g
            d = per_act.setdefault(r.act_id, {"count": 0, "lo": None, "hi": None})
            d["count"] += 1
            g = order_of.get(r.id)
            if g is not None:
                d["lo"] = g if d["lo"] is None else min(d["lo"], g)
                d["hi"] = g if d["hi"] is None else max(d["hi"], g)

        focus_source = "view"
        focus_order = order_of.get(focus_chapter_id) if focus_chapter_id else None
        if focus_order is None:
            focus_source = "content_break"
            focus_order = first_empty
        if focus_order is None:
            focus_source = "start"
            focus_order = 1 if total else 0

        lo = max(1, focus_order - self._WINDOW_RADIUS) if focus_order else 1
        hi = min(total, focus_order + self._WINDOW_RADIUS) if total else 0
        window_ids = ordered[lo - 1:hi] if hi else []

        def brief(cid) -> dict | None:
            m = meta.get(cid)
            if m is None:
                return None
            return {
                "id": str(cid),
                "global_order": order_of.get(cid),
                "title": m.title or "",
                "status": m.status or "",
                "body_chars": int(m.body_chars or 0),
            }

        acts_data = []
        for act in acts:
            d = per_act.get(act.id, {})
            chapters_data = []
            for cid in window_ids:
                m = meta.get(cid)
                if m is not None and m.act_id == act.id:
                    entry = brief(cid)
                    if entry is not None:
                        chapters_data.append(entry)
            acts_data.append({
                "id": str(act.id),
                "name": act.name,
                "sort_order": act.sort_order,
                "purpose": (act.purpose or "")[:200],
                "chapter_count": int(d.get("count", 0)),
                "first_chapter_order": d.get("lo"),
                "last_chapter_order": d.get("hi"),
                # **只含窗口内的章**（旧实现是全量）。要总数请看 chapter_count。
                "chapters": chapters_data,
            })

        focus = None
        if focus_order and total:
            entry = brief(ordered[focus_order - 1])
            if entry is not None:
                focus = {**entry, "source": focus_source,
                         "is_written": entry["body_chars"] > 0}

        content_break = None
        if first_empty and total:
            entry = brief(ordered[first_empty - 1])
            if entry is not None:
                content_break = entry

        global_settings = proj.global_settings or ""
        spine = proj.spine or ""
        ending = proj.ending or ""
        characters_data, relations_data, setups_data = await self._build_side_records(
            proj.id, order_of, lean=True
        )
        available_skills = await self._get_available_skills()

        return {
            "project": {
                "id": str(proj.id), "title": proj.title, "genre": proj.genre or "",
                "logline": proj.logline or "",
                "spine": spine[:1200], "spine_chars": len(spine),
                "ending": ending[:1200], "ending_chars": len(ending),
                "total_words": int(proj.total_words or 0), "status": proj.status or "",
                "global_settings": global_settings[:2000],
                "global_settings_chars": len(global_settings),
            },
            "acts": acts_data,
            "chapter_count": total,
            "written_chapters": written_total,
            "chapter_window": {
                "lo": lo, "hi": hi, "radius": self._WINDOW_RADIUS,
                "listed": len(window_ids),
                "focus": focus, "content_break": content_break,
            },
            "characters": characters_data,
            "relations": relations_data,
            "setups": setups_data,
            "available_skills": available_skills,
        }

    async def build_summary(
        self,
        project_id: uuid.UUID,
        query_hint: str = "",
        depth: str = "minimal",
        skip_cache: bool = False,
        focus_chapter_id: uuid.UUID | None = None,
    ) -> dict:
        """框架树 + 侧挂记录，供主循环每轮使用。

        ``depth``：

        - ``framework``：**常驻地图**——幕图（全部）+ 侧挂记录 + 窗口内的章目录
          （以 ``focus_chapter_id`` 或正文断点为中心，见
          :meth:`_build_windowed_framework`）；正文一律不进；
        - ``summary`` / ``full``：加上章 goal 与 result 全文、人物的
          「他想要什么 / 他从哪到哪」、以及伏笔明细——**全量章条目**，
          给一次性的分析用，不适合每轮跑。

        ``framework`` 的缓存键带上焦点章：窗口是跟着"你在看哪一章"走的，
        不带焦点会把别的章节的窗口喂回来。
        """
        lean = depth in ("framework", "minimal")
        ck = self._cache_key(
            project_id, "summary",
            f"{depth}|{focus_chapter_id}" if lean else depth,
        )
        if not skip_cache:
            cached = await self._cache_get(ck)
            if cached is not None:
                return dict(cached)

        proj = await self._get_project(project_id)
        if not proj:
            return {}

        if lean:
            result = await self._build_windowed_framework(proj, focus_chapter_id)
            await self._cache_set(ck, result)
            return result

        tree = await self._load_project_tree(project_id)
        acts = tree["acts"]
        chapters_by_act = tree["chapters_by_act"]
        body_chars = tree.get("body_chars", {})

        # 全局章号跨幕连续——它是算出来的，不存（规格判据②）。
        global_order = {
            chapter.id: index + 1
            for index, chapter in enumerate(tree["all_chapters"])
        }

        acts_data = []
        for act in acts:
            chapters_data = []
            for chapter in chapters_by_act.get(act.id, []):
                chapters_data.append({
                    "id": str(chapter.id),
                    "global_order": global_order.get(chapter.id),
                    "title": chapter.title,
                    "sort_order": chapter.sort_order,
                    "status": chapter.status or "",
                    "body_chars": int(body_chars.get(chapter.id, 0)),
                    "goal": chapter.goal or "",
                    "result": chapter.result or "",
                })
            acts_data.append({
                "id": str(act.id),
                "name": act.name,
                "sort_order": act.sort_order,
                "purpose": act.purpose or "",
                "chapter_count": len(chapters_data),
                "first_chapter_order": global_order.get(
                    chapters_by_act.get(act.id, [None])[0]
                ) if chapters_by_act.get(act.id) else None,
                "last_chapter_order": global_order.get(
                    chapters_by_act.get(act.id, [None])[-1]
                ) if chapters_by_act.get(act.id) else None,
                "chapters": chapters_data,
            })

        characters_data, relations_data, setups_data = await self._build_side_records(
            project_id, global_order, lean=False
        )
        available_skills = await self._get_available_skills()

        global_settings = proj.global_settings or ""
        spine = proj.spine or ""
        ending = proj.ending or ""

        result = {
            "project": {
                "id": str(proj.id), "title": proj.title, "genre": proj.genre or "",
                "logline": proj.logline or "",
                "spine": spine, "spine_chars": len(spine),
                "ending": ending, "ending_chars": len(ending),
                "total_words": int(proj.total_words or 0), "status": proj.status or "",
                "global_settings": global_settings[:2000],
                "global_settings_chars": len(global_settings),
            },
            "acts": acts_data,
            "characters": characters_data,
            "relations": relations_data,
            "setups": setups_data,
            "available_skills": available_skills,
            "chapter_count": len(tree["all_chapters"]),
        }

        note = self._truncation_note(tree)
        if note:
            result["truncated_note"] = note

        # RAG 与 query 有关、且只有最终回复那一遍会消费，所以从不写进共享的
        # 每项目缓存（否则一个会话的 query 会污染其他会话的快照）。
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

    async def build_for_writing(self, chapter_id: uuid.UUID, action: str = "write") -> dict:
        """给写作 Agent 的专注上下文——**按章**，不再按场。

        只装一个写作 agent 需要的东西：这一章要写什么、它在整本书里的位置、
        上一章写成了什么样、人物此刻要什么、已有正文。

        没有视角字段：视角写在章蓝图里（规格第 11 节范围界定第 4 条）。
        跨章连续性靠 ``previous_chapter_result`` 对接——这就是"上一章写成了
        什么"必须写回的原因（第 7 步）。
        """
        ctx: dict[str, Any] = {}

        # 这一章要读正文（续写/重写都要看见已有正文），所以显式 undefer。
        chapter = (await self.db.execute(
            with_chapter_content(select(Chapter).where(Chapter.id == chapter_id))
        )).scalar_one_or_none()
        if not chapter:
            return ctx

        ctx["chapter_title"] = chapter.title or ""
        ctx["chapter_goal"] = chapter.goal or ""
        # 只放 writer.yaml 真正会用的键。章的 result/status/正文字数都不给：
        # 它们对写手没有信息量（正文本身给了 existing_content），多一个键就是
        # 一份要维护、会和模板悄悄漂移的载荷——测试 test_writer_context.py 的
        # UNUSED_CTX_KEYS 就是钉这件事的。

        existing_content = chapter.content or ""
        if existing_content:
            ctx["existing_content"] = existing_content
            if action in ("continue", "rewrite"):
                ctx["existing_content_tail"] = existing_content[-1500:]

        ordered = await ordered_chapter_ids(self.db, chapter.project_id)
        try:
            position = ordered.index(chapter.id)
            ctx["chapter_number"] = position + 1
        except ValueError:
            ctx["chapter_number"] = chapter.sort_order
            position = None

        # 上一章：跨章连续性唯一的载体（第 1 步）
        if position is not None and position > 0:
            prev = (await self.db.execute(
                with_chapter_content(select(Chapter).where(Chapter.id == ordered[position - 1]))
            )).scalar_one_or_none()
            if prev:
                ctx["previous_chapter_title"] = prev.title or ""
                ctx["previous_chapter_result"] = prev.result or ""
                prev_content = prev.content or ""
                if prev_content:
                    # 上一章结尾原文是"接缝 + 语感"的唯一来源：800 字够写手
                    # 接上时空、句长与对白分寸，也比 500 字更像一段完整的话。
                    ctx["previous_chapter_tail"] = prev_content[-_PREV_TAIL_CHARS:]

        # 下一章要写什么：**不给写手自由发挥的余地，只给它一个停点**。
        # 写手最常见的越界是"为了给后面铺路"把下一章的事件提前写掉。
        if position is not None and position + 1 < len(ordered):
            nxt = (await self.db.execute(
                select(Chapter).where(Chapter.id == ordered[position + 1])
            )).scalar_one_or_none()
            if nxt and ((nxt.title or "").strip() or (nxt.goal or "").strip()):
                ctx["next_chapter_title"] = (nxt.title or "").strip()
                ctx["next_chapter_goal"] = _clip_text(nxt.goal or "", 400)

        # 篇幅参考：本书已写章的平均正文长度（写手原来只拿到"由分量决定"）。
        ctx["length_target"] = await self._length_target_text(chapter.project_id, chapter.id)

        # 幕与全书位置（第 3 步）
        act = (await self.db.execute(
            select(Act).where(Act.id == chapter.act_id)
        )).scalar_one_or_none()
        ctx["act_name"] = act.name if act else ""
        ctx["act_purpose"] = (act.purpose or "") if act else ""

        project = await self._get_project(chapter.project_id)
        if project:
            ctx["project_title"] = project.title or ""
            ctx["genre"] = project.genre or ""
            ctx["logline"] = project.logline or ""
            ctx["ending"] = project.ending or ""
            ctx["spine"] = project.spine or ""
            settings_text = (project.global_settings or "").strip()
            ctx["global_settings"] = (
                _clip_text(settings_text, _GLOBAL_SETTINGS_BUDGET) if settings_text else ""
            )

        # 人物（第 4 步）：每个人的「他想要什么」+「他从哪到哪」
        ctx["characters_text"] = await self._characters_text(chapter.project_id)

        # 还没收的伏笔：这一章可能轮到它（第 6 步）
        setups = (await self.db.execute(
            select(Setup).where(Setup.project_id == chapter.project_id, Setup.status == "open")
            .order_by(*order_by_sequence(Setup))
        )).scalars().all()
        order_map = {cid: index + 1 for index, cid in enumerate(ordered)}
        if setups:
            ctx["open_setups_text"] = "\n".join(
                "- " + (s.note or "（未描述）")
                + (f"（第 {order_map[s.planted_in]} 章埋下）" if order_map.get(s.planted_in) else "")
                + (f"（打算第 {order_map[s.pay_off_in]} 章收）" if s.pay_off_in and order_map.get(s.pay_off_in) else "（还没定在哪收）")
                for s in setups
            )

        return ctx

    async def _get_project(self, project_id: uuid.UUID):
        r = await self.db.execute(select(Project).where(Project.id == project_id))
        return r.scalar_one_or_none()

    async def _characters_text(self, project_id: uuid.UUID) -> str:
        """人物清单：静态档案只给摘要，重点是「他想要什么」和「他从哪到哪」。"""
        result = await self.db.execute(
            select(Character).where(Character.project_id == project_id)
            .order_by(*order_by_sequence(Character))
        )
        chars = result.scalars().all()
        if not chars:
            return "暂无人物"
        used = 0
        lines: list[str] = []
        names_only: list[str] = []
        for char in chars:
            role = _ROLE_LABELS.get((char.role or "").strip(), char.role or "未指定")
            parts = [f"- {char.name}（{role}）"]
            for field, label, value in (
                ("appearance", "外貌", char.appearance or ""),
                ("personality", "性格", char.personality or ""),
                ("motivation", "他想要什么", char.motivation or ""),
                ("arc", "他从哪到哪", char.arc or ""),
                ("background", "背景", char.background or ""),
            ):
                if value.strip():
                    parts.append(f"  {label}：{_clip_text(value, _CHAR_FIELD_LIMITS[field])}")
            block = "\n".join(parts)
            if names_only or used + len(block) + 1 > _CHARACTERS_BUDGET:
                names_only.append(f"- {char.name}（{role}）")
                continue
            used += len(block) + 1
            lines.append(block)
        if names_only:
            # 名单**一个都不能丢**：写手不知道还有谁存在，就会自己造一个人顶上去。
            # 超出预算的这次只给名字，档案按需再读。
            lines.append(
                "（以下人物这次只列了名字，档案没有全带上——正文里需要他们时按名字与"
                "角色定位处理，不要替他们改写已有的设定：）"
            )
            lines.extend(names_only)
        return "\n".join(lines)

    async def _length_target_text(self, project_id: uuid.UUID, chapter_id: uuid.UUID) -> str:
        """篇幅参考：本书已写章的正文平均字数。

        写手原来唯一的篇幅指令是「篇幅由这一章的分量决定」——没有数字。实测的
        后果是同一个蓝图写成 1500 字和写成 5000 字都算"合乎要求"。给一个**下限**
        区间，并说明这个数是怎么来的（本书自己的平均），比给上限安全：模型不会
        为了够数字灌水，但会知道写短了不对劲。
        """
        rows = (await self.db.execute(
            select(func.length(func.coalesce(Chapter.content, "")))
            .where(Chapter.project_id == project_id, Chapter.id != chapter_id)
        )).scalars().all()
        written = sorted(int(n or 0) for n in rows if int(n or 0) >= _LENGTH_MIN_WRITTEN)
        if written:
            average = sum(written) // len(written)
            lo, hi = int(average * 0.8), int(average * 1.35)
            basis = f"本书已写 {len(written)} 章，平均 {average} 字"
        else:
            lo, hi = _LENGTH_FALLBACK
            basis = "这本书还没有正文，按单章常规体量"
        lo = max(800, lo // 100 * 100)
        hi = max(lo + 600, hi // 100 * 100)
        return (
            f"{lo}–{hi} 字（{basis}）。写到 {lo} 字以下，通常说明该停留的地方没写透；"
            "这一章的分量值得更长，就写更长。"
        )

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

    async def _decorate_setups(
        self, setups_data: list[dict], global_order: dict[uuid.UUID, int]
    ) -> list[dict]:
        """把伏笔两端的章 id 解析成全局章号与章名，让这一行能直接被 LLM 消费。"""
        if not setups_data:
            return setups_data
        wanted: set[uuid.UUID] = set()
        for row in setups_data:
            for key in ("planted_in", "pay_off_in"):
                value = row.get(key)
                if not value:
                    continue
                try:
                    wanted.add(uuid.UUID(str(value)))
                except (ValueError, TypeError):
                    continue
        titles: dict[str, str] = {}
        if wanted:
            result = await self.db.execute(
                select(Chapter.id, Chapter.title).where(Chapter.id.in_(list(wanted)))
            )
            titles = {str(k): (v or "") for k, v in result.all()}
        for row in setups_data:
            for key in ("planted_in", "pay_off_in"):
                raw = row.get(key)
                parsed = None
                if raw:
                    try:
                        parsed = uuid.UUID(str(raw))
                    except (ValueError, TypeError):
                        parsed = None
                row[f"{key}_order"] = global_order.get(parsed) if parsed else None
                row[f"{key}_title"] = titles.get(str(parsed), "") if parsed else ""
        return setups_data

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
