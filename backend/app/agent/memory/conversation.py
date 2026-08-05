from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory.models import Conversation, ConversationMessage
from app.llm.types import Message, ToolCall


_CONV_PREFIX = "conv:"
_CONV_META_PREFIX = "conv_meta:"
_CONV_MSGS_PREFIX = "conv_msgs:"
_SET_PREFIX = "conv_set:"
_MAX_IN_MEMORY_CONVERSATIONS = 500
_IN_MEMORY_TTL = 86400 * 7
MAX_HISTORY_MESSAGES = 200

# Redis is now only a cache. TTL bounds how long a stale entry can survive if
# the DB write path was interrupted; the DB remains the source of truth.
_CACHE_TTL = 86400 * 7


def _msg_to_dict(m: Message) -> dict[str, Any]:
    d: dict[str, Any] = {"role": m.role}
    if m.content is not None:
        d["content"] = m.content
    if m.tool_calls:
        d["tool_calls"] = [
            {"id": tc.id, "type": tc.type, "function": tc.function}
            for tc in m.tool_calls
        ]
    if m.tool_call_id:
        d["tool_call_id"] = m.tool_call_id
    if m.name:
        d["name"] = m.name
    return d


def _dict_to_msg(d: dict) -> Message:
    return Message(
        role=d.get("role", "user"),
        content=d.get("content"),
        tool_calls=[ToolCall(**tc) for tc in d.get("tool_calls", [])] if d.get("tool_calls") else None,
        tool_call_id=d.get("tool_call_id"),
        name=d.get("name"),
    )


def _to_uuid(value: str) -> uuid.UUID:
    """Best-effort string -> UUID conversion for legacy string inputs."""
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return uuid.UUID(int=0)


def _canonical_id(value: str) -> str:
    """Return a stable canonical form for a conversation id."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return str(value)


class ConversationMemory:
    """Conversation persistence backed by PostgreSQL, with Redis as cache.

    Design (see docs/conversation_persistence.md):
      * ``conversations`` + ``conversation_messages`` tables are the source
        of truth — they survive Redis eviction, restart, and scale-out.
      * Redis caches hot message lists / metadata; every entry is
        rebuildable from the DB and bounded by ``_CACHE_TTL``.
    """

    def __init__(self, db: AsyncSession, redis_client: Redis | None = None):
        self.db = db
        self._redis = redis_client
        self._lock = asyncio.Lock()

    # ── Cache helpers ──────────────────────────────────────────────────

    async def _cache_meta(self, conv: Conversation) -> None:
        if not self._redis:
            return
        try:
            meta_key = f"{_CONV_PREFIX}{conv.id}"
            await self._redis.hset(
                meta_key,
                mapping={
                    "project_id": str(conv.project_id),
                    "user_id": str(conv.user_id),
                    "title": conv.title or "",
                },
            )
            await self._redis.expire(meta_key, _CACHE_TTL)
        except Exception as exc:
            logger.debug("conv cache meta write failed: {}", exc)

    async def _cache_messages(self, conversation_id: str, msgs: list[dict]) -> None:
        if not self._redis:
            return
        try:
            key = f"{_CONV_MSGS_PREFIX}{conversation_id}"
            pipe = self._redis.pipeline()
            pipe.delete(key)
            for d in msgs:
                pipe.rpush(key, json.dumps(d))
            pipe.expire(key, _CACHE_TTL)
            await pipe.execute()
        except Exception as exc:
            logger.debug("conv cache messages write failed: {}", exc)

    async def _cache_agent_state(self, conversation_id: str, data: dict) -> None:
        if not self._redis:
            return
        try:
            key = f"{_CONV_META_PREFIX}{conversation_id}"
            await self._redis.hset(key, "agent_state", json.dumps(data, ensure_ascii=False))
            await self._redis.expire(key, _CACHE_TTL)
        except Exception as exc:
            logger.debug("conv cache agent_state write failed: {}", exc)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def create_conversation(self, project_id: str, user_id: str, title: str = "") -> str:
        conv = Conversation(
            id=uuid.uuid4(),
            project_id=_to_uuid(project_id),
            user_id=_to_uuid(user_id),
            title=title or "",
        )
        self.db.add(conv)
        await self.db.commit()
        await self._cache_meta(conv)
        if self._redis:
            try:
                await self._redis.sadd(f"{_SET_PREFIX}{project_id}:{user_id}", str(conv.id))
                await self._redis.expire(f"{_SET_PREFIX}{project_id}:{user_id}", _CACHE_TTL)
            except Exception as exc:
                logger.debug("conv cache set add failed: {}", exc)
        return str(conv.id)

    async def rename_conversation(self, conversation_id: str, title: str) -> None:
        cid = _canonical_id(conversation_id)
        conv = await self._get_conversation(cid)
        if conv is None:
            return
        conv.title = title
        await self.db.commit()
        await self._cache_meta(conv)

    async def delete_conversation(self, project_id: str, user_id: str, conversation_id: str) -> bool:
        cid = _canonical_id(conversation_id)
        conv = await self._get_conversation(cid)
        if conv is None:
            return False
        if str(conv.project_id) != str(_to_uuid(project_id)) or str(conv.user_id) != str(_to_uuid(user_id)):
            return False
        await self.db.delete(conv)
        await self.db.commit()
        if self._redis:
            try:
                pipe = self._redis.pipeline()
                pipe.delete(f"{_CONV_PREFIX}{cid}")
                pipe.delete(f"{_CONV_MSGS_PREFIX}{cid}")
                pipe.delete(f"{_CONV_META_PREFIX}{cid}")
                pipe.srem(f"{_SET_PREFIX}{project_id}:{user_id}", cid)
                await pipe.execute()
            except Exception as exc:
                logger.debug("conv cache delete failed: {}", exc)
        return True

    # ── Messages ──────────────────────────────────────────────────────

    async def get_history(self, conversation_id: str) -> list[Message]:
        cid = _canonical_id(conversation_id)
        # Cache hit path
        if self._redis:
            try:
                raw = await self._redis.lrange(f"{_CONV_MSGS_PREFIX}{cid}", 0, -1)
                if raw:
                    return [_dict_to_msg(json.loads(item)) for item in raw]
            except Exception as exc:
                logger.debug("conv cache history read failed: {}", exc)

        rows = await self._load_history_rows(cid)
        msgs = [_dict_to_msg(r.data) for r in rows]
        if msgs:
            await self._cache_messages(cid, [_msg_to_dict(m) for m in msgs])
        return msgs

    async def _load_history_rows(self, conversation_id: str) -> list[ConversationMessage]:
        if _to_uuid(conversation_id).int == 0:
            return []
        result = await self.db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == _to_uuid(conversation_id))
            .order_by(ConversationMessage.id)
            .limit(MAX_HISTORY_MESSAGES)
        )
        return list(result.scalars().all())

    async def save_message(self, conversation_id: str, message: Message) -> None:
        cid = _canonical_id(conversation_id)
        if _to_uuid(cid).int == 0:
            return
        # Trim to the newest MAX_HISTORY_MESSAGES in the DB as well.
        count = await self._count_messages(cid)
        if count >= MAX_HISTORY_MESSAGES:
            overflow = count - MAX_HISTORY_MESSAGES + 1
            await self._trim_messages(cid, overflow)
        self.db.add(ConversationMessage(
            conversation_id=_to_uuid(cid),
            data=_msg_to_dict(message),
        ))
        await self.db.commit()
        if self._redis:
            try:
                key = f"{_CONV_MSGS_PREFIX}{cid}"
                pipe = self._redis.pipeline()
                pipe.rpush(key, json.dumps(_msg_to_dict(message)))
                pipe.ltrim(key, -MAX_HISTORY_MESSAGES, -1)
                pipe.expire(key, _CACHE_TTL)
                await pipe.execute()
            except Exception as exc:
                logger.debug("conv cache message append failed: {}", exc)

    async def _count_messages(self, conversation_id: str) -> int:
        result = await self.db.execute(
            select(func.count(ConversationMessage.id))
            .where(ConversationMessage.conversation_id == _to_uuid(conversation_id))
        )
        return int(result.scalar() or 0)

    async def _trim_messages(self, conversation_id: str, keep_count: int) -> None:
        subq = (
            select(ConversationMessage.id)
            .where(ConversationMessage.conversation_id == _to_uuid(conversation_id))
            .order_by(ConversationMessage.id)
            .limit(keep_count)
        )
        ids = [r[0] for r in await self.db.execute(subq)]
        if ids:
            await self.db.execute(
                delete(ConversationMessage).where(ConversationMessage.id.in_(ids))
            )

    async def replace_history(self, conversation_id: str, messages: list[Message]) -> None:
        cid = _canonical_id(conversation_id)
        if _to_uuid(cid).int == 0:
            return
        await self.db.execute(
            delete(ConversationMessage).where(ConversationMessage.conversation_id == _to_uuid(cid))
        )
        for m in messages:
            self.db.add(ConversationMessage(
                conversation_id=_to_uuid(cid),
                data=_msg_to_dict(m),
            ))
        await self.db.commit()
        await self._cache_messages(cid, [_msg_to_dict(m) for m in messages])

    # ── Conversation list / detail ────────────────────────────────────

    async def list_conversations(self, project_id: str, user_id: str) -> list[dict]:
        result = await self.db.execute(
            select(Conversation)
            .where(
                Conversation.project_id == _to_uuid(project_id),
                Conversation.user_id == _to_uuid(user_id),
            )
            .order_by(Conversation.created_at.desc())
        )
        convs = []
        for conv in result.scalars().all():
            convs.append({
                "id": str(conv.id),
                "project_id": str(conv.project_id),
                "user_id": str(conv.user_id),
                "title": conv.title or "",
                "created_at": self._iso(conv.created_at),
            })
        return convs

    async def get_conversation(self, project_id: str, user_id: str, conversation_id: str) -> dict | None:
        cid = _canonical_id(conversation_id)
        conv = await self._get_conversation(cid)
        if conv is None:
            return None
        if str(conv.project_id) != str(_to_uuid(project_id)) or str(conv.user_id) != str(_to_uuid(user_id)):
            return None
        messages = await self.get_history(cid)
        return {
            "id": str(conv.id),
            "project_id": str(conv.project_id),
            "user_id": str(conv.user_id),
            "title": conv.title or "",
            "created_at": self._iso(conv.created_at),
            "messages": [
                {
                    "id": f"{cid}-{i}",
                    "role": m.role,
                    "content": m.content or "",
                    "created_at": self._iso(conv.created_at),
                }
                for i, m in enumerate(messages)
            ],
        }

    # ── Agent state ───────────────────────────────────────────────────

    async def save_agent_state(
        self,
        conversation_id: str,
        pending_plan: dict,
        current_options: list[dict],
        plan_confirmed: bool = False,
        mode: str = "chat",
        cowriter_session: dict | None = None,
        id_registry: dict | None = None,
        id_registry_version: int = 0,
    ) -> None:
        cid = _canonical_id(conversation_id)
        conv = await self._get_conversation(cid)
        if conv is None:
            return
        try:
            data = {
                "pending_plan": pending_plan,
                "current_options": current_options,
                "plan_confirmed": bool(plan_confirmed),
                "mode": mode,
                "cowriter_session": cowriter_session or {},
                "id_registry": id_registry or {},
                "id_registry_version": int(id_registry_version or 0),
            }
            # Round-trip through JSON to guarantee JSON-serializability
            json.dumps(data, ensure_ascii=False)
            conv.agent_state = data
            await self.db.commit()
            await self._cache_agent_state(cid, data)
        except (TypeError, ValueError) as e:
            logger.error("Failed to serialize agent state: {}", e)

    async def load_agent_state(self, conversation_id: str) -> tuple:
        cid = _canonical_id(conversation_id)
        state: dict | None = None

        # Cache read first
        if self._redis:
            try:
                raw = await self._redis.hget(f"{_CONV_META_PREFIX}{cid}", "agent_state")
                if raw:
                    state = json.loads(raw)
            except Exception as exc:
                logger.debug("conv cache agent_state read failed: {}", exc)

        if state is None:
            conv = await self._get_conversation(cid)
            if conv is not None and conv.agent_state:
                state = conv.agent_state
                if self._redis:
                    await self._cache_agent_state(cid, state)

        if not state:
            return {}, [], False, "chat", {}, {}, 0
        try:
            return (
                state.get("pending_plan", {}),
                state.get("current_options", []),
                state.get("plan_confirmed", False),
                state.get("mode", "chat"),
                state.get("cowriter_session", {}),
                state.get("id_registry", {}) or {},
                int(state.get("id_registry_version", 0) or 0),
            )
        except (json.JSONDecodeError, TypeError):
            return {}, [], False, "chat", {}, {}, 0

    # ── Summary position ──────────────────────────────────────────────

    async def get_summary_count(self, conversation_id: str) -> int:
        conv = await self._get_conversation(_canonical_id(conversation_id))
        return conv.summary_count if conv else 0

    async def set_summary_count(self, conversation_id: str, count: int) -> None:
        conv = await self._get_conversation(_canonical_id(conversation_id))
        if conv is None:
            return
        conv.summary_count = int(count)
        await self.db.commit()

    # ── Helpers ───────────────────────────────────────────────────────

    async def _get_conversation(self, conversation_id: str) -> Conversation | None:
        if _to_uuid(conversation_id).int == 0:
            return None
        result = await self.db.execute(
            select(Conversation).where(Conversation.id == _to_uuid(conversation_id))
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _iso(dt: Any) -> str:
        if dt is None:
            return ""
        if isinstance(dt, (int, float)):
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(dt))
        return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
