"""Post-turn hook system for the autonomous agent loop.

After each turn completes (tools executed or response generated), hooks
run for bookkeeping: state tracking, resource cleanup, usage logging.

Inspired by Claude Code's ``stopHooks.ts``, but simplified — only the
hooks that make sense for AiStoryCAD's use case.

Usage::

    from app.agent.hooks import hook_registry

    @hook_registry.register("post_turn")
    async def log_turn_usage(state, llm_client):
        ...  # Log token usage, errors, etc.

Hooks run sequentially and must not raise (errors are logged and
swallowed).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

PostTurnHook = Callable[..., Awaitable[None]]


@dataclass
class _HookEntry:
    name: str
    fn: PostTurnHook
    once: bool = False  # If True, runs only once then auto-unregisters
    ran: bool = False


class HookRegistry:
    """Registry for post-turn hooks.

    Hooks are executed in registration order.  Errors are logged and
    do not propagate — a failing hook never blocks subsequent hooks or
    the agent loop itself.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[_HookEntry]] = {}

    def register(
        self,
        event: str,
        name: str | None = None,
        once: bool = False,
    ) -> Callable:
        """Decorator to register a hook for *event*.

        Args:
            event: Hook event name (e.g. ``"post_turn"``, ``"post_error"``).
            name: Human-readable name (defaults to function ``__name__``).
            once: If True, runs only once.
        """
        def decorator(fn: PostTurnHook) -> PostTurnHook:
            entry = _HookEntry(
                name=name or fn.__name__,
                fn=fn,
                once=once,
            )
            self._hooks.setdefault(event, []).append(entry)
            return fn
        return decorator

    def unregister(self, event: str, name: str) -> None:
        """Remove a hook by name."""
        self._hooks.setdefault(event, [])
        self._hooks[event] = [h for h in self._hooks[event] if h.name != name]

    async def run(self, event: str, **kwargs: Any) -> None:
        """Run all hooks registered for *event*.

        Args:
            event: Event name.
            **kwargs: Passed to each hook function.
        """
        entries = self._hooks.get(event, [])
        to_remove: list[_HookEntry] = []
        for entry in entries:
            try:
                start = time.monotonic()
                await entry.fn(**kwargs)
                elapsed = time.monotonic() - start
                if elapsed > 1.0:
                    logger.warning("Hook '{}.{}' took {:.1f}s", event, entry.name, elapsed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Hook '{}.{}' failed", event, entry.name)
            if entry.once and not entry.ran:
                entry.ran = True
                to_remove.append(entry)
        for entry in to_remove:
            self._hooks[event].remove(entry)


# ── Global registry ─────────────────────────────────────────────────

hook_registry = HookRegistry()


# ── Built-in hooks ──────────────────────────────────────────────────


@hook_registry.register("post_turn", name="log_turn_metrics")
async def _log_turn_metrics(**kwargs: Any) -> None:
    """Log timing and message count after each turn."""
    state = kwargs.get("state")
    llm_client = kwargs.get("llm_client")
    turn_start = kwargs.get("turn_start")
    if state is None or turn_start is None:
        return
    elapsed = time.monotonic() - turn_start
    msg_count = len(state.messages)
    logger.info(
        "Turn {} complete | mode={} | msgs={} | errors={} | "
        "transition={} | elapsed={:.2f}s",
        state.turn_count, state.mode, msg_count,
        len(state.errors), state.transition, elapsed,
    )


@hook_registry.register("post_error", name="log_error")
async def _log_error(**kwargs: Any) -> None:
    """Log errors with consistent format."""
    state = kwargs.get("state")
    error = kwargs.get("error", "")
    if state:
        logger.error(
            "Turn {} error | transition={} | retry={}/{} | error={:.200}",
            state.turn_count, state.transition,
            state.retry_count, state.max_retries, error,
        )


@hook_registry.register("post_turn", name="check_model_override")
async def _check_model_override(**kwargs: Any) -> None:
    """Log when a fallback model is active."""
    state = kwargs.get("state")
    if state and state._model_override:
        logger.info("Model override active: {}", state._model_override)
