from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from loguru import logger
from redis.asyncio import Redis

from app.llm.types import Message, ToolCall


_CONV_PREFIX = "conv:"
_CONV_META_PREFIX = "conv_meta:"
_CONV_MSGS_PREFIX = "conv_msgs:"
_SET_PREFIX = "conv_set:"
MAX_HISTORY_MESSAGES = 200
_MAX_IN_MEMORY_CONVERSATIONS = 500
_IN_MEMORY_TTL = 86400 * 7


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


class ConversationMemory:
    def __init__(self, redis_client: Redis | None = None):
        self._redis = redis_client
        self._lock = asyncio.Lock()
        self._store: dict[str, list[dict]] = {}
        self._meta: dict[str, dict] = {}
        self._store_access: dict[str, float] = {}

    async def _evict_if_needed(self):
        if len(self._store) <= _MAX_IN_MEMORY_CONVERSATIONS:
            return
        now = time.time()
        cutoff = now - _IN_MEMORY_TTL
        expired = [k for k, t in self._store_access.items() if t < cutoff]
        if expired:
            for k in expired:
                self._store.pop(k, None)
                self._meta.pop(k, None)
                self._store_access.pop(k, None)
        while len(self._store) > _MAX_IN_MEMORY_CONVERSATIONS:
            oldest = min(self._store_access, key=self._store_access.get)
            self._store.pop(oldest, None)
            self._meta.pop(oldest, None)
            self._store_access.pop(oldest, None)

    async def create_conversation(self, project_id: str, user_id: str, title: str = "") -> str:
        conv_id = str(uuid.uuid4())
        now = time.time()
        meta = {"project_id": project_id, "user_id": user_id, "title": title, "created_at": now}
        if self._redis:
            pipe = self._redis.pipeline()
            pipe.hset(
                f"{_CONV_PREFIX}{conv_id}",
                mapping={"project_id": project_id, "user_id": user_id, "title": title},
            )
            pipe.sadd(f"{_SET_PREFIX}{project_id}:{user_id}", conv_id)
            await pipe.execute()
        else:
            async with self._lock:
                self._meta[conv_id] = meta
                self._store[conv_id] = []
                self._store_access[conv_id] = now
        return conv_id

    async def rename_conversation(self, conversation_id: str, title: str) -> None:
        if self._redis:
            await self._redis.hset(f"{_CONV_PREFIX}{conversation_id}", "title", title)
        else:
            async with self._lock:
                if conversation_id in self._meta:
                    self._meta[conversation_id]["title"] = title

    async def get_history(self, conversation_id: str) -> list[Message]:
        if self._redis:
            raw = await self._redis.lrange(
                f"{_CONV_MSGS_PREFIX}{conversation_id}", 0, -1
            )
            msgs = []
            for item in raw:
                d = json.loads(item)
                msgs.append(_dict_to_msg(d))
            return msgs
        async with self._lock:
            raw = self._store.get(conversation_id, [])
        return [_dict_to_msg(d) for d in raw]

    async def save_message(self, conversation_id: str, message: Message) -> None:
        d = _msg_to_dict(message)
        if self._redis:
            pipe = self._redis.pipeline()
            pipe.rpush(f"{_CONV_MSGS_PREFIX}{conversation_id}", json.dumps(d))
            pipe.ltrim(f"{_CONV_MSGS_PREFIX}{conversation_id}", -MAX_HISTORY_MESSAGES, -1)
            pipe.expire(f"{_CONV_MSGS_PREFIX}{conversation_id}", 86400 * 7)
            await pipe.execute()
        else:
            async with self._lock:
                if conversation_id not in self._store:
                    self._store[conversation_id] = []
                if conversation_id not in self._meta:
                    self._meta[conversation_id] = {"project_id": "", "user_id": "", "title": ""}
                self._store[conversation_id].append(d)
                self._store_access[conversation_id] = time.time()
                if len(self._store[conversation_id]) > MAX_HISTORY_MESSAGES:
                    self._store[conversation_id] = self._store[conversation_id][-MAX_HISTORY_MESSAGES:]
                await self._evict_if_needed()

    async def list_conversations(self, project_id: str, user_id: str) -> list[dict]:
        if self._redis:
            conv_ids = await self._redis.smembers(f"{_SET_PREFIX}{project_id}:{user_id}")
            convs = []
            for cid_bytes in conv_ids:
                cid = cid_bytes.decode() if isinstance(cid_bytes, bytes) else cid_bytes
                data = await self._redis.hgetall(f"{_CONV_PREFIX}{cid}")
                if data:
                    decoded = {}
                    for k, v in data.items():
                        k_str = k.decode() if isinstance(k, bytes) else k
                        v_str = v.decode() if isinstance(v, bytes) else v
                        decoded[k_str] = v_str
                    decoded["id"] = cid
                    convs.append(decoded)
            return convs
        async with self._lock:
            result = []
            for cid, meta in self._meta.items():
                if meta.get("project_id") == project_id and meta.get("user_id") == user_id:
                    result.append({"id": cid, **meta})
            return result

    async def get_conversation(self, project_id: str, user_id: str, conversation_id: str) -> dict | None:
        if self._redis:
            data = await self._redis.hgetall(f"{_CONV_PREFIX}{conversation_id}")
            if data:
                decoded = {}
                for k, v in data.items():
                    k_str = k.decode() if isinstance(k, bytes) else k
                    v_str = v.decode() if isinstance(v, bytes) else v
                    decoded[k_str] = v_str
                if decoded.get("project_id") != project_id or decoded.get("user_id") != user_id:
                    return None
                # Include messages for frontend ConversationDetail contract
                messages = await self.get_history(conversation_id)
                decoded["messages"] = [
                    {
                        "id": f"{conversation_id}-{i}",
                        "role": m.role,
                        "content": m.content or "",
                        "created_at": decoded.get("created_at", ""),
                    }
                    for i, m in enumerate(messages)
                ]
                return decoded
            return None
        async with self._lock:
            meta = self._meta.get(conversation_id)
        if meta and meta.get("project_id") == project_id and meta.get("user_id") == user_id:
            messages = await self.get_history(conversation_id)
            meta["messages"] = [
                {
                    "id": f"{conversation_id}-{i}",
                    "role": m.role,
                    "content": m.content or "",
                    "created_at": meta.get("created_at", ""),
                }
                for i, m in enumerate(messages)
            ]
            return meta
        return None

    async def save_agent_state(self, conversation_id: str, pending_plan: dict, current_options: list[dict], plan_confirmed: bool = False, mode: str = "chat", cowriter_session: dict | None = None, id_registry: dict | None = None, id_registry_version: int = 0) -> None:
        try:
            data = json.dumps({
                "pending_plan": pending_plan,
                "current_options": current_options,
                "plan_confirmed": plan_confirmed,
                "mode": mode,
                "cowriter_session": cowriter_session or {},
                "id_registry": id_registry or {},
                "id_registry_version": int(id_registry_version or 0),
            }, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logger.error("Failed to serialize agent state: {}", e)
            return
        if self._redis:
            await self._redis.hset(f"{_CONV_PREFIX}{conversation_id}", "agent_state", data)
            await self._redis.expire(f"{_CONV_PREFIX}{conversation_id}", 86400 * 7)
        else:
            async with self._lock:
                if conversation_id in self._meta:
                    self._meta[conversation_id]["agent_state"] = data

    async def load_agent_state(self, conversation_id: str) -> tuple:
        raw = None
        if self._redis:
            raw = await self._redis.hget(f"{_CONV_PREFIX}{conversation_id}", "agent_state")
        else:
            async with self._lock:
                meta = self._meta.get(conversation_id, {})
                raw = meta.get("agent_state")
        if raw:
            try:
                state = json.loads(raw)
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
        return {}, [], False, "chat", {}, {}, 0

    async def replace_history(self, conversation_id: str, messages: list[Message]) -> None:
        """Atomically replace all stored messages for a conversation."""
        dicts = [_msg_to_dict(m) for m in messages]
        if self._redis:
            pipe = self._redis.pipeline()
            key = f"{_CONV_MSGS_PREFIX}{conversation_id}"
            pipe.delete(key)
            for d in dicts:
                pipe.rpush(key, json.dumps(d))
            pipe.expire(key, 86400 * 7)
            await pipe.execute()
        else:
            async with self._lock:
                self._store[conversation_id] = dicts
                self._store_access[conversation_id] = time.time()

    async def delete_conversation(self, project_id: str, user_id: str, conversation_id: str) -> bool:
        if self._redis:
            belongs = await self._redis.sismember(
                f"{_SET_PREFIX}{project_id}:{user_id}", conversation_id
            )
            if not belongs:
                return False
            pipe = self._redis.pipeline()
            pipe.delete(f"{_CONV_PREFIX}{conversation_id}")
            pipe.delete(f"{_CONV_MSGS_PREFIX}{conversation_id}")
            pipe.srem(f"{_SET_PREFIX}{project_id}:{user_id}", conversation_id)
            await pipe.execute()
            return True
        async with self._lock:
            meta = self._meta.get(conversation_id)
            if meta and meta.get("project_id") == project_id and meta.get("user_id") == user_id:
                self._meta.pop(conversation_id, None)
                self._store.pop(conversation_id, None)
                self._store_access.pop(conversation_id, None)
                return True
            return False