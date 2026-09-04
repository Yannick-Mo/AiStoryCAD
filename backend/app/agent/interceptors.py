"""Interceptor layer for the autonomous agent loop.

Two gates applied in order after the LLM produces tool calls but before
execution:

1. **Mode Gate** — chat mode blocks write tools
2. **Confirmation Gate** — destructive/needs-confirmation tools require user approval

Inspired by Claude Code's permission system but adapted for AiStoryCAD's
domain: chat safety and write confirmation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.tools.base import BaseTool

logger = logging.getLogger(__name__)

# ── Destructive / confirmation gating ──────────────────────────────────────────────
#
# Destructive-tool gating is driven by ToolMeta.needs_confirmation and
# ToolMeta.is_destructive, NOT by a hardcoded name list.
#
# To mark a tool as needing confirmation, set ``meta.needs_confirmation = True``
# in the tool class.  The interceptor checks this flag regardless of the
# hardcoded sets.

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class InterceptResult:
    """Result of applying all interceptors to one turn's tool calls."""

    # True if at least one tool was blocked (stop the turn, inject error)
    blocked: bool = False
    blocked_tools: list[str] = field(default_factory=list)

    # True if at least one tool needs user confirmation before executing
    needs_confirmation: bool = False
    # Tools waiting for confirmation: list of (tool_name, args, tool_use_id)
    pending_tools: list[tuple[str, dict, str]] = field(default_factory=list)

    # Tools that passed all gates (can execute immediately)
    allowed_tools: list[tuple[str, dict, str]] = field(default_factory=list)

    # Messages to inject for blocked tools (error context for LLM)
    blocked_messages: list[str] = field(default_factory=list)


# ── Interceptor functions ────────────────────────────────────────────────────


def apply_interceptors(
    tool_calls: list[tuple[str, dict, str]],  # [(tool_name, args, tool_use_id), ...]
    mode: str,
    cowriter_session: dict | None = None,
    tools_registry: dict[str, "BaseTool"] | None = None,
) -> InterceptResult:
    """Apply both gates to a batch of tool calls.

    Args:
        tool_calls: List of (tool_name, args, tool_use_id) tuples.
        mode: ``"chat"`` or ``"cowriter"``.
        cowriter_session: Reserved (kept for call-site compatibility).
        tools_registry: Optional tool lookup for meta attributes.

    Returns:
        ``InterceptResult`` summarizing which tools are blocked, confirmed,
        or allowed through.
    """
    result = InterceptResult()
    session = cowriter_session or {}

    for tool_name, args, tool_use_id in tool_calls:
        # ── Gate 1: Mode Gate ──
        if mode == "chat":
            tool = tools_registry.get(tool_name) if tools_registry else None
            is_read = (
                not getattr(tool, "is_write_operation", True)
                if tool
                else _is_read_by_name(tool_name)
            )
            if not is_read:
                result.blocked = True
                result.blocked_tools.append(tool_name)
                result.blocked_messages.append(
                    f"对话模式禁止写入操作。工具 '{tool_name}' 被拦截（对话模式仅允许读工具）。"
                    f"请向用户说明：需要切换到协作模式才能执行写入操作。"
                )
                continue

        # ── Gate 2: Confirmation Gate ──
        if _needs_confirmation(tool_name, session, tools_registry):
            result.needs_confirmation = True
            result.pending_tools.append((tool_name, args, tool_use_id))
            continue

        # ── Passed all gates ──
        result.allowed_tools.append((tool_name, args, tool_use_id))

    return result


def build_confirmation_plan(
    pending_tools: list[tuple[str, dict, str]],
    tools_registry: dict[str, "BaseTool"] | None = None,
) -> dict:
    """Build a plan object for frontend confirmation UI.

    Args:
        pending_tools: List of (tool_name, args, tool_use_id) tuples that
                       need user confirmation before execution.
        tools_registry: Optional tool lookup for human-readable descriptions.

    Returns:
        A dict with ``steps`` and ``reasoning`` fields suitable for the
        ``pending_plan`` SSE event.
    """
    steps = []
    for tool_name, args, tool_use_id in pending_tools:
        tool = tools_registry.get(tool_name) if tools_registry else None
        desc = (
            tool._effective_description[:100] if tool and tool.meta
            else tool_name
        )
        steps.append({
            "tool": tool_name,
            "description": desc,
            "params": args,
            "tool_use_id": tool_use_id,
        })

    reasoning = (
        f"以下 {len(pending_tools)} 个写入操作需要你的确认才能执行："
        + "、".join(s["description"] for s in steps)
    )

    return {
        "steps": steps,
        "reasoning": reasoning,
        "status": "awaiting_confirmation",
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_read_by_name(name: str) -> bool:
    """Heuristic for read-only tools when registry is unavailable."""
    read_prefixes = ("read_", "list_", "search_", "analyze_", "check_", "suggest_", "web_search", "web_fetch")
    return name.startswith(read_prefixes) or name in ("project_health",)


def _needs_confirmation(
    tool_name: str,
    cowriter_session: dict,
    tools_registry: dict[str, "BaseTool"] | None = None,
) -> bool:
    """Determine if a tool call needs user confirmation.

    Rules (checked in order, first match wins):
    1. ToolMeta.needs_confirmation=True (canonical marker, checked via registry).
    """
    # Rule 1: Meta-driven gating (canonical)
    if tools_registry:
        tool = tools_registry.get(tool_name)
        if tool and hasattr(tool, "meta") and tool.meta is not None:
            if getattr(tool.meta, "needs_confirmation", False):
                return True

    return False
