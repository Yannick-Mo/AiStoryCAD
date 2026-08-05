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

from app.config import settings

if TYPE_CHECKING:
    from app.llm.types import Message

logger = logging.getLogger(__name__)

# ── Default model context limits (tokens) ────────────────────────────
DEFAULT_MODEL_LIMIT = settings.llm_context_window or 100_000  # compression triggers at ~80% of this

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

    summary_parts: list[str] = []
    for msg in middle:
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

    result = list(head) + [summary] + list(tail)

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

    # Strategy: keep head + tail from non-tool messages, DROP all tool
    # messages.  Entity IDs are covered by the per-turn ID registry, so
    # verbatim tool-message preservation is no longer needed.
    non_tool: list[M] = [
        m for m in messages
        if m.role != "tool"
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

    result = list(head) + [summary] + list(tail)

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


_LIGHTER_SUMMARY_PROMPT = """你是一个对话摘要助手。请用中文详细摘要以下小说创作助手与用户的对话。

要求：
- 保留用户的关键需求、创作决策、情节设定
- 保留 AI 给出的重要建议和已执行的操作
- 标记任何待办事项或未完成的任务
- 保留所有实体 ID 和名称（角色名、章节名等）
- 尽量保留对话中的重要细节和上下文信息
- 摘要控制在 500 字以内

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
    threshold: float = COMPRESS_THRESHOLD,
    summary_prompt: str | None = None,
) -> list["Message"]:
    """Async compression using LLM-generated summary for the middle section.

    Falls back to synchronous ``compress_history`` if the LLM call fails,
    so compression always succeeds (degraded but never skipped).

    Args:
        threshold: token ratio below which compression is skipped (0.0 = always compress).
        summary_prompt: custom prompt template with {text} placeholder. None uses default.
    """
    from app.llm.types import Message as M

    tokens = estimate_tokens(messages, model_limit)
    ratio = tokens / model_limit if model_limit else 0

    if ratio <= threshold:
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

    if not middle:
        return list(messages)

    summary_text = ""
    try:
        formatted = _format_for_summary(middle)
        prompt = (summary_prompt or _SUMMARY_PROMPT).format(text=formatted)
        result = await llm_chat([M(role="user", content=prompt)], max_tokens=1024, temperature=0.3)
        summary_text = (result.content or "").strip()
    except Exception:
        logger.warning(
            "LLM summary failed — falling back to truncation-based compression",
            exc_info=True,
        )
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

    result = list(head) + [summary] + list(tail)
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
