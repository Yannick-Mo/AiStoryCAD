"""Conversation history management: summarization, sliding window, pruning."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger
from redis.asyncio import Redis

from app.agent.context_compressor import estimate_tokens
from app.config import settings
from app.llm.client import LLMClient
from app.llm.types import Message

# ── Thresholds, derived from the model context window ─────────────────
# The agent loop compresses at ~80% of the window (context_compressor).
# Summarize BEFORE that so long history is folded into a summary instead of
# being truncated by the emergency path.  (MAX_HISTORY_TOKENS_EST used to be
# 200K — larger than the window — so summarization never fired in time.)
_MODEL_CONTEXT_WINDOW = settings.llm_context_window or 120_000
MAX_HISTORY_TOKENS_EST = int(_MODEL_CONTEXT_WINDOW * 0.75)
MAX_HISTORY_TOKENS_HARD = _MODEL_CONTEXT_WINDOW
SUMMARIZE_AFTER = 30
KEEP_RECENT = 15
MIN_SUMMARIZE_INTERVAL = 10

# Redis key prefix for persisted summary state
_SUMMARY_PREFIX = "conv:summary:"

# Pattern to extract tool name from tool message content like:
# "[工具执行结果: list_chapters]\n..." or "[工具执行失败: update_chapter]\n..."
_TOOL_MSG_PATTERN = re.compile(r'^\[工具执行(?:结果|失败): (\w+)\]')


def _sanitize_for_summary(messages: list[Message]) -> list[Message]:
    """Remove internal tool details, keep only essential info.

    Tool messages are collapsed to ``[Tool executed: name]`` placeholders —
    entity IDs are covered by the per-turn ID registry, so verbatim tool-result
    preservation is no longer needed here.
    """
    sanitized: list[Message] = []
    for m in messages:
        if m.role == "tool":
            tool_name = _extract_tool_name(m.content or "")
            sanitized.append(Message(
                role="tool",
                content=f"[Tool executed: {tool_name or 'unknown'}]",
                tool_call_id=m.tool_call_id,
            ))
        elif m.tool_calls:
            sanitized.append(Message(
                role=m.role,
                content=m.content,
                tool_calls=None,
            ))
        else:
            sanitized.append(m)
    return sanitized


def _extract_tool_name(content: str) -> str | None:
    """Extract tool name from tool message content.

    Matches:
      [工具执行结果: list_chapters]
      [工具执行失败: update_chapter]
    """
    m = _TOOL_MSG_PATTERN.match(content)
    return m.group(1) if m else None


class HistoryManager:
    def __init__(
        self,
        llm_client: LLMClient | None = None,
        redis_client: Redis | None = None,
        conversation_id: str | None = None,
        conv_memory: Any | None = None,
    ):
        self._llm_client = llm_client
        self._redis_client = redis_client
        self._conversation_id = conversation_id
        self._conv_memory = conv_memory
        self._last_summary_count = 0

    async def restore_last_summary_count(self) -> None:
        """Restore ``_last_summary_count`` from Redis if available."""
        if not self._redis_client or not self._conversation_id:
            return
        try:
            raw = await self._redis_client.get(f"{_SUMMARY_PREFIX}{self._conversation_id}")
            if raw:
                self._last_summary_count = int(raw)
        except Exception:
            logger.debug("Could not restore summary count from Redis (non-critical)")

    async def _save_summary_position(self, count: int) -> None:
        """Persist ``_last_summary_count`` to Redis."""
        if self._redis_client and self._conversation_id:
            try:
                await self._redis_client.set(
                    f"{_SUMMARY_PREFIX}{self._conversation_id}",
                    str(count),
                    ex=86400 * 7,
                )
            except Exception:
                logger.debug("Could not persist summary count to Redis (non-critical)")

    @property
    def llm(self) -> LLMClient:
        if self._llm_client is None:
            from app.llm.client import get_shared_client
            self._llm_client = get_shared_client()
        return self._llm_client

    async def maybe_summarize(self, messages: list[Message]) -> list[Message]:
        user_msgs = [m for m in messages if m.role == "user"]
        total_tokens = estimate_tokens(messages)

        if len(user_msgs) <= SUMMARIZE_AFTER and total_tokens <= MAX_HISTORY_TOKENS_EST:
            return messages

        if len(messages) - self._last_summary_count < MIN_SUMMARIZE_INTERVAL:
            return messages

        try:
            summary = await self._summarize_old(messages)
        except Exception:
            logger.exception("Summarization failed, keeping original messages")
            return messages

        if not summary or summary == "[摘要生成失败]":
            return messages

        recent = messages[-KEEP_RECENT * 2:] if len(messages) > KEEP_RECENT * 2 else messages

        self._last_summary_count = len(messages)
        await self._save_summary_position(len(messages))
        summary_msg = Message(role="system", content=f"[至此的对话摘要]:\n{summary}")

        result = [summary_msg] + recent

        if estimate_tokens(result) > MAX_HISTORY_TOKENS_HARD:
            result = [summary_msg] + recent[-KEEP_RECENT:]

        # Persist the summarized history back to conv_memory
        if self._conv_memory and self._conversation_id:
            try:
                await self._conv_memory.replace_history(self._conversation_id, result)
                logger.info(
                    "Persisted summarized history: %d msgs → %d msgs",
                    len(messages), len(result),
                )
            except Exception:
                logger.warning("Failed to persist summarized history (non-critical)")

        return result

    async def _summarize_old(self, messages: list[Message]) -> str:
        cleaned = _sanitize_for_summary(messages)
        cutoff = min(len(cleaned), KEEP_RECENT * 2)
        old = cleaned[:-cutoff] if cutoff > 0 else cleaned
        recent = cleaned[-cutoff:] if cutoff > 0 else []

        old_text = ""
        for m in old:
            role = "User" if m.role == "user" else "Assistant"
            old_text += f"{role}: {(m.content or '')[:500]}\n"

        recent_text = ""
        for m in recent:
            role = "User" if m.role == "user" else "Assistant"
            recent_text += f"{role}: {(m.content or '')[:300]}\n"

        prompt = (
            "请用中文摘要以下小说创作助手与用户的对话。\n"
            "保留：用户的关键需求、已做出的创作决策、项目变更、以及待办事项。\n\n"
            "较早的消息：\n" + old_text + "\n"
            "最近的消息（供参考上下文）：\n" + recent_text + "\n\n"
            "摘要："
        )

        msgs = [Message(role="user", content=prompt)]
        try:
            result = await self.llm.chat(messages=msgs, temperature=0.3, max_tokens=1024)
            return result.content or ""
        except Exception:
            logger.exception("LLM summarization call failed")
            return "[摘要生成失败]"
