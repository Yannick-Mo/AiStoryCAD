"""Token budget tracking for the autonomous agent loop.

Tracks estimated token consumption per turn and proactively warns the
model when the context window is near capacity.  The model can then
trim its response to avoid hitting the hard ceiling mid-stream.

Inspired by Claude Code's ``checkTokenBudget()`` in ``query/tokenBudget.ts``
but adapted for DeepSeek's 128K context window without auto-continue.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.agent.context_compressor import estimate_tokens, estimate_text_tokens
from app.config import settings

if TYPE_CHECKING:
    from app.llm.types import Message

# ── Thresholds (as fraction of model context window) ────────────────

# When to inject a proactive "budget warning" into the system prompt.
# The model is told to keep its response short.
WARN_AT_RATIO = 0.80

# When to inject a **strong** budget warning.
CRITICAL_AT_RATIO = 0.95

# Token buffer between our estimated budget and the model's true
# context limit.  The model also needs room for the new response and
# any tool results produced during the turn.
SAFETY_BUFFER_TOKENS = 50_000

# Maximum tokens we budget for the model's response.
MAX_RESPONSE_TOKENS = 8_192

# Maximum tokens we budget for tool results produced this turn.
MAX_TOOL_RESULT_TOKENS = 100_000

# How many bytes of a tool result constitute "large" (triggers summary).
LARGE_TOOL_RESULT_BYTES = 2_000

# Default model limit (tokens), read from settings or fallback.
_DEFAULT_MODEL_LIMIT = settings.llm_context_window or 120_000

# ── Budget tracker dataclass ────────────────────────────────────────

CONTINUATION_LIMIT = 3


@dataclass
class TurnBudget:
    """Per-turn token budget state, embedded in ``LoopState``."""

    total_estimated_tokens: int = 0
    model_limit: int = _DEFAULT_MODEL_LIMIT
    continuation_count: int = 0
    last_delta_tokens: int = 0
    started_at: float = 0.0


# ── Public API ──────────────────────────────────────────────────────


def compute_budget(
    messages: list["Message"],
    tool_results: list[dict],
    model_limit: int = _DEFAULT_MODEL_LIMIT,
) -> TurnBudget:
    """Compute the current token budget snapshot.

    Returns:
        A ``TurnBudget`` with ``total_estimated_tokens`` populated.
    """
    msg_tokens = estimate_tokens(messages, model_limit)
    # Add estimated tool result tokens
    tr_tokens = sum(
        estimate_text_tokens(str(r.get("data", ""))) + estimate_text_tokens(str(r.get("error", "")))
        for r in tool_results
    )
    return TurnBudget(
        total_estimated_tokens=msg_tokens + tr_tokens,
        model_limit=model_limit,
        started_at=time.time(),
    )


def check_token_budget(budget: TurnBudget) -> dict:
    """Check the current token budget and return a budget directive.

    Returns a dict that can be injected into the system prompt:
    ``{"warn": "critical" | "warning" | "", "message": "..."}``
    """
    used = budget.total_estimated_tokens
    limit = budget.model_limit
    ratio = used / limit if limit > 0 else 0

    available = limit - used - SAFETY_BUFFER_TOKENS

    if ratio >= CRITICAL_AT_RATIO:
        return {
            "warn": "critical",
            "message": (
                f"[Token Budget CRITICAL: context at {ratio:.0%} ({used:,}/{limit:,} tokens). "
                f"Keep responses concise and avoid unnecessary tool calls.]"
            ),
            "available": max(available, 0),
        }

    if ratio >= WARN_AT_RATIO:
        return {
            "warn": "warning",
            "message": (
                f"[Token Budget: context at {ratio:.0%} ({used:,}/{limit:,} tokens). "
                f"Remaining budget for this turn: ~{available:,} tokens.]"
            ),
            "available": max(available, 0),
        }

    return {"warn": "", "message": "", "available": max(available, 0)}


def check_turn_continuation(
    budget: TurnBudget,
    new_tokens: int,
) -> dict:
    """Check whether we should issue a continuation nudge.

    Returns ``{"continue": bool, "nudge": str}``.
    Mirrors Claude Code's ``checkTokenBudget()`` logic:
    - Stop if we've had too many continuations already.
    - Continue if the model is still producing substantial output.
    - Stop on diminishing returns (small deltas after 3+ continuations).
    """
    delta = new_tokens - budget.total_estimated_tokens
    is_first = budget.continuation_count == 0

    if budget.continuation_count >= CONTINUATION_LIMIT:
        return {"continue": False, "nudge": ""}

    # Diminishing returns: if we've continued 3+ times and the last
    # two deltas were tiny, stop.
    if budget.continuation_count >= 2 and delta < 500 and budget.last_delta_tokens < 500:
        return {"continue": False, "nudge": ""}

    # Under budget and producing content — continue
    used_pct = new_tokens / budget.model_limit if budget.model_limit else 0
    if used_pct < 0.85 and delta > 200:
        nudge = (
            f"[Continue working. You were at {used_pct:.0%} of context. "
            f"Do NOT summarise. Do NOT stop early.]"
        )
        return {"continue": True, "nudge": nudge}

    return {"continue": False, "nudge": ""}
