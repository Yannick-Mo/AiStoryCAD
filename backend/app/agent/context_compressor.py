"""Token-aware context compression for the autonomous agent loop.

Two compression layers, inspired by Claude Code's multi-layer pipeline
(``src/services/compact/``):

1. **compress_history** — proactive, triggered at 80% threshold.
   ``head + summary + tail`` classic strategy.
2. **reactive_compress** — last-resort, triggered on API 413 / context
   overflow.  More aggressive than proactive compress.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from app.llm.types import Message

logger = logging.getLogger(__name__)

# ── Default model context limits (tokens) ────────────────────────────
DEFAULT_MODEL_LIMIT = 100_000  # compression triggers at ~80% of this (~80K tokens)

# ── Threshold ratios ────────────────────────────────────────────────
COMPRESS_THRESHOLD = 0.80
AGGRESSIVE_THRESHOLD = 0.95

# ── Message retention counts ───────────────────────────────────────
DEFAULT_HEAD_COUNT = 5
DEFAULT_TAIL_COUNT = 12
AGGRESSIVE_HEAD_COUNT = 3
AGGRESSIVE_TAIL_COUNT = 8
REACTIVE_HEAD_COUNT = 2
REACTIVE_TAIL_COUNT = 5

# Tools whose results carry entity IDs that downstream tools depend on.
# These must survive all compression layers so the LLM can chain
# list_* → extract ID → write_* across turns without re-querying.
_ID_SOURCE_TOOLS: set[str] = {
    "list_chapters", "list_scenes", "list_characters",
    "list_relations", "list_edges", "read_full_project",
    "read_chapter", "read_scene", "read_project_overview",
    "read_character", "search_nodes",
}


def _is_id_source_msg(msg: "Message") -> bool:
    """Return True if *msg* is a tool result from an ID-source tool."""
    if msg.role != "tool":
        return False
    content = msg.content or ""
    if not content.startswith("[工具执行结果:"):
        return False
    end = content.find("]")
    if end > 0:
        tool_name = content[7:end].strip()
        return tool_name in _ID_SOURCE_TOOLS
    return False


def estimate_text_tokens(text: str) -> int:
    """CJK-aware token estimation for a plain text string.

    CJK chars ≈ 1.5 tokens, ASCII ≈ 0.25 tokens.
    """
    if not text:
        return 0
    cjk = sum(
        1
        for c in text
        if "一" <= c <= "鿿" or "　" <= c <= "〿" or "＀" <= c <= "￯"
    )
    ascii_count = len(text) - cjk
    return int(cjk * 1.5 + ascii_count * 0.25) + 1


def estimate_tokens(messages: list["Message"], model_limit: int = DEFAULT_MODEL_LIMIT) -> int:
    """Estimate the token count of a message list.

    Delegates to ``estimate_text_tokens`` per message (CJK-aware).
    Fast enough to run every turn, accurate enough for threshold-based decisions.
    """
    total = 0
    for msg in messages:
        content = getattr(msg, "content", "") or ""
        total += estimate_text_tokens(content)
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                fn = getattr(tc, "function", {})
                args_str = str(fn.get("arguments", "")) if isinstance(fn, dict) else ""
                total += estimate_text_tokens(args_str)
    return total


def should_compress(
    messages: list["Message"],
    model_limit: int = DEFAULT_MODEL_LIMIT,
) -> bool:
    """Return True if the message list should be compressed before the next call."""
    tokens = estimate_tokens(messages, model_limit)
    return tokens > int(model_limit * COMPRESS_THRESHOLD)


# ── Layer 1: Proactive compress (head + summary + tail) ────────────


def _msg_role_label(role: str) -> str:
    return {"user": "用户", "assistant": "AI", "system": "系统"}.get(role, role)


def compress_history(
    messages: list["Message"],
    *,
    model_limit: int = DEFAULT_MODEL_LIMIT,
    head_count: int | None = None,
    tail_count: int | None = None,
) -> list["Message"]:
    """Compress a message list by summarizing the middle portion.

    Returns a new list: ``head + [summary] + tail``.

    If the list is short enough to not need compression, it's returned
    unchanged.
    """
    from app.llm.types import Message as M

    tokens = estimate_tokens(messages, model_limit)
    ratio = tokens / model_limit if model_limit else 0

    if ratio <= COMPRESS_THRESHOLD:
        return list(messages)

    if ratio > AGGRESSIVE_THRESHOLD:
        h = head_count if head_count is not None else AGGRESSIVE_HEAD_COUNT
        t = tail_count if tail_count is not None else AGGRESSIVE_TAIL_COUNT
    else:
        h = head_count if head_count is not None else DEFAULT_HEAD_COUNT
        t = tail_count if tail_count is not None else DEFAULT_TAIL_COUNT

    if len(messages) <= h + t + 2:
        return list(messages)

    head = messages[:h]
    tail = messages[-t:]
    middle = messages[h:-t]

    # Extract ID-source tool messages from middle so they survive compression
    preserved_id_msgs: list[M] = []
    rest_middle: list[M] = []
    for msg in middle:
        if _is_id_source_msg(msg):
            preserved_id_msgs.append(msg)
        else:
            rest_middle.append(msg)

    summary_parts: list[str] = []
    for msg in rest_middle:
        role = _msg_role_label(getattr(msg, "role", "unknown"))
        content = (getattr(msg, "content", "") or "")[:200]
        if content:
            summary_parts.append(f"[{role}]: {content}")

    kept = summary_parts[-15:] if len(summary_parts) > 15 else summary_parts

    summary = M(
        role="system",
        content=(
            "<system-reminder>\n"
            "[已压缩的历史上下文 — 以下是之前对话的摘要]\n"
            + "\n".join(kept)
            + "\n</system-reminder>"
        ),
    )

    result = list(head) + [summary] + preserved_id_msgs + list(tail)

    new_tokens = estimate_tokens(result, model_limit)
    logger.info(
        "Context compressed: %d msgs → %d msgs, ~%d → ~%d tokens",
        len(messages), len(result), tokens, new_tokens,
    )

    return result


# ── Layer 2: Reactive compress (413 / context overflow) ────────────


def reactive_compress(
    messages: list["Message"],
    *,
    model_limit: int = DEFAULT_MODEL_LIMIT,
) -> list["Message"]:
    """Aggressive compression for API 413 / context overflow recovery.

    More aggressive than ``compress_history``:
    - Keeps only first 1 message + last 3 messages.
    - Drops all tool messages (not user/assistant).

    This is a **last resort** — it heavily truncates context to free
    enough room for the model to continue.

    Inspired by Claude Code's ``reactiveCompact`` triggered on
    ``prompt_too_long`` error.
    """
    from app.llm.types import Message as M

    h = REACTIVE_HEAD_COUNT
    t = REACTIVE_TAIL_COUNT

    if len(messages) <= h + t + 1:
        return compress_history(
            messages, model_limit=model_limit,
            head_count=h, tail_count=t,
        )

    # Strategy: keep head + tail from non-tool messages, DROP all
    # non-ID-source tool messages, but PRESERVE ID-source tool messages
    # so the LLM can still look up entity IDs for downstream tool calls.
    id_source_msgs: list[M] = [m for m in messages if _is_id_source_msg(m)]
    non_tool: list[M] = [
        m for m in messages
        if m.role != "tool" or _is_id_source_msg(m)
    ]
    if len(non_tool) <= h + t:
        # Fall back to aggressive compress if stripping tools isn't enough
        return compress_history(
            messages, model_limit=model_limit,
            head_count=h, tail_count=t,
        )

    head = non_tool[:h]
    tail = non_tool[-t:]

    middle = non_tool[h:-t]
    summary_parts: list[str] = []
    for msg in middle:
        role_label = _msg_role_label(getattr(msg, "role", "unknown"))
        content = (getattr(msg, "content", "") or "")[:100]
        if content:
            summary_parts.append(f"[{role_label}]: {content}")

    kept = summary_parts[-8:] if len(summary_parts) > 8 else summary_parts
    summary = M(
        role="system",
        content=(
            "<system-reminder>\n"
            "[紧急上下文压缩 — 上下文过长已被截断]\n"
            + "\n".join(kept)
            + "\n</system-reminder>"
        ),
    )

    # Keep a reasonable number of ID-source messages at the end
    preserved = id_source_msgs[-3:] if len(id_source_msgs) > 3 else id_source_msgs
    result = list(head) + [summary] + list(tail) + preserved

    tokens_before = estimate_tokens(messages, model_limit)
    tokens_after = estimate_tokens(result, model_limit)
    logger.warning(
        "Reactive compress: %d msgs → %d msgs, ~%d → ~%d tokens (%.0f%% reduction)",
        len(messages), len(result), tokens_before, tokens_after,
        (1 - tokens_after / max(tokens_before, 1)) * 100,
    )

    return result


# ── Layer 3: LLM-powered compression (async, real summary) ────────


def _format_for_summary(messages: list["Message"]) -> str:
    """Format messages into a text block suitable for LLM summarization."""
    lines: list[str] = []
    for m in messages:
        role_label = {"user": "用户", "assistant": "AI", "system": "系统", "tool": "工具"}.get(m.role, m.role)
        content = (m.content or "")[:800]
        if m.role == "tool":
            tool_name = content.split("\n")[0] if "\n" in content else content[:80]
            lines.append(f"[{role_label}]: {tool_name}")
        else:
            lines.append(f"[{role_label}]: {content}")
    return "\n\n".join(lines)


_SUMMARY_PROMPT = """你是一个对话摘要助手。请用简洁的中文摘要以下对话内容。

要求：
- 保留用户的关键需求、创作决策、情节设定
- 保留 AI 给出的重要建议和已执行的操作
- 标记任何待办事项或未完成的任务
- 保留所有实体 ID 和名称（角色名、章节名等）
- 摘要控制在 300 字以内

对话内容：
{text}

摘要："""


async def async_compress_context(
    messages: list["Message"],
    llm_chat: Callable[..., Any],
    *,
    model_limit: int = DEFAULT_MODEL_LIMIT,
    head_count: int | None = None,
    tail_count: int | None = None,
) -> list["Message"]:
    """Async compression using LLM-generated summary for the middle section.

    Falls back to synchronous ``compress_history`` if the LLM call fails,
    so compression always succeeds (degraded but never skipped).
    """
    from app.llm.types import Message as M

    tokens = estimate_tokens(messages, model_limit)
    ratio = tokens / model_limit if model_limit else 0

    if ratio <= COMPRESS_THRESHOLD:
        return list(messages)

    h = head_count if head_count is not None else DEFAULT_HEAD_COUNT
    t = tail_count if tail_count is not None else DEFAULT_TAIL_COUNT
    if ratio > AGGRESSIVE_THRESHOLD:
        h = AGGRESSIVE_HEAD_COUNT
        t = AGGRESSIVE_TAIL_COUNT

    if len(messages) <= h + t + 2:
        return list(messages)

    head = messages[:h]
    tail = messages[-t:]
    middle = messages[h:-t]

    preserved_id_msgs: list[M] = []
    rest_middle: list[M] = []
    for msg in middle:
        if _is_id_source_msg(msg):
            preserved_id_msgs.append(msg)
        else:
            rest_middle.append(msg)

    if not rest_middle:
        return list(head) + preserved_id_msgs + list(tail)

    summary_text = ""
    try:
        formatted = _format_for_summary(rest_middle)
        prompt = _SUMMARY_PROMPT.format(text=formatted)
        result = await llm_chat([M(role="user", content=prompt)], max_tokens=1024, temperature=0.3)
        summary_text = (result.content or "").strip()
    except Exception:
        logger.warning("LLM summary failed — falling back to truncation-based compression")
        return compress_history(messages, model_limit=model_limit, head_count=head_count, tail_count=tail_count)

    summary = M(
        role="system",
        content=(
            "<system-reminder>\n"
            "[以下为之前对话的 LLM 摘要]\n"
            f"{summary_text}\n"
            "</system-reminder>"
        ),
    )

    result = list(head) + [summary] + preserved_id_msgs + list(tail)
    new_tokens = estimate_tokens(result, model_limit)
    logger.info(
        "LLM context compressed: %d msgs → %d msgs, ~%d → ~%d tokens",
        len(messages), len(result), tokens, new_tokens,
    )
    return result


def build_boundary_message(original_count: int, compressed_count: int, reactive: bool = False) -> "Message":
    """Create a user-visible boundary message for context compression events."""
    from app.llm.types import Message as M

    if reactive:
        label = "紧急压缩"
        detail = "由于上下文长度超出限制，已截断部分内容"
    else:
        label = "上下文自动压缩"
        detail = "由于对话较长，已将之前的对话内容压缩为摘要"

    return M(
        role="system",
        content=(
            f"[{label}：已将之前的 {original_count} 条消息"
            f"压缩为 {compressed_count} 条以保持响应质量。{detail}。最近的对话内容未受影响。]"
        ),
    )
