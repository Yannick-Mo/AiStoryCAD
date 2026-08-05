"""LoopState — immutable state container for the autonomous agent loop.

Modeled after Claude Code's State aggregator pattern in query.ts.
Every "continue" site returns a new instance via :meth:`replace`, guaranteeing
atomic state transitions.

This is the SINGLE canonical state container for the agent system.
The old ``AgentState`` TypedDict (``app/agent/state.py``) has been removed.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from app.llm.types import Message


@dataclass
class LoopState:
    """Immutable-style state for the model-driven agent loop.

    All mutations go through :meth:`replace`, which returns a brand-new
    instance via ``dataclasses.replace``.  No field should be mutated
    directly — treat this like a value object.
    """

    # ── Identifiers ──────────────────────────────────────────────────
    project_id: str = ""
    user_id: str = ""
    conversation_id: str = ""
    trace_id: str = ""

    # ── Mode ────────────────────────────────────────────────────────
    mode: str = "chat"  # "chat" | "cowriter"

    # ── Conversation ────────────────────────────────────────────────
    messages: list[Message] = field(default_factory=list)
    project_context: dict = field(default_factory=dict)
    active_skills: list = field(default_factory=list)

    # ── Tool execution ──────────────────────────────────────────────
    tool_results: list[dict] = field(default_factory=list)

    # ── Entity ID registry ──────────────────────────────────────────
    # ``id_registry`` holds the *merged* registry (persisted baseline + this
    # request's tool-result window), rebuilt each turn.  ``_id_registry_persisted``
    # is the immutable baseline loaded from Redis at request start.  Version is
    # bumped once per request; ``seen`` counters use it to expire stale entries.
    id_registry: dict = field(default_factory=dict)
    id_registry_version: int = 1
    _id_registry_persisted: dict = field(default_factory=dict)

    # ── Cowriter session ────────────────────────────────────────────
    cowriter_session: dict = field(default_factory=dict)
    current_options: list[dict] = field(default_factory=list)

    # ── Planning ────────────────────────────────────────────────────
    pending_plan: dict = field(default_factory=dict)
    plan_confirmed: bool = False
    planned_steps: list = field(default_factory=list)
    current_step_index: int = 0

    # ── Recovery / errors ───────────────────────────────────────────
    errors: list = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 3
    retry_context: dict | None = None
    recovery_state: dict = field(default_factory=dict)
    _model_override: str = ""

    # ── Token budget ────────────────────────────────────────────────
    budget_total_estimated: int = 0
    budget_model_limit: int = 900_000
    budget_continuation_count: int = 0
    budget_last_delta: int = 0
    budget_warn_level: str = ""  # "" | "warning" | "critical"

    # ── Tool-only loop detection ────────────────────────────────────
    # Separate counter so error recovery (which uses retry_count) does
    # not pollute tool-only loop detection, and the retry_count=0 reset
    # at loop.py:848 does not prevent accumulation.
    tool_only_turns: int = 0
    # Tracks consecutive write-only turns to catch infinite write loops
    write_only_turns: int = 0

    # ── Context compression scan tracker ────────────────────────────
    _last_scan_count: int = 0

    # ── Lifecycle ───────────────────────────────────────────────────
    turn_count: int = 0
    _context_loaded: bool = False
    _invalidated_sections: set = field(default_factory=set)
    transition: str = ""  # diagnostic: last action taken

    # ── Public API ──────────────────────────────────────────────────

    def replace(self, **overrides) -> "LoopState":
        """Return a new ``LoopState`` with *overrides* applied.

        Uses ``dataclasses.replace`` so all fields are preserved except
        those explicitly overridden.  Zero-cost to add new fields —
        ``replace`` handles them automatically.
        """
        return dataclasses.replace(self, **overrides)

    # ── Factory / serialization ──────────────────────────────────────

    @classmethod
    def from_initial(cls, initial_state: dict) -> "LoopState":
        """Build a ``LoopState`` from a flat state dictionary.

        The previous code path (LangGraph) built ``AgentState`` dicts.
        This method accepts any dict and maps known keys onto
        ``LoopState`` fields with safe defaults.
        """
        return cls(
            # Identifiers
            project_id=initial_state.get("project_id", "") or "",
            user_id=initial_state.get("user_id", "") or "",
            conversation_id=initial_state.get("conversation_id", "") or "",
            trace_id=initial_state.get("trace_id", "") or "",
            # Mode
            mode=initial_state.get("mode", "chat") or "chat",
            # Conversation
            messages=list(initial_state.get("messages", []) or []),
            project_context=dict(initial_state.get("project_context", {}) or {}),
            active_skills=list(initial_state.get("active_skills", []) or []),
            # Tool execution
            tool_results=list(initial_state.get("tool_results", []) or []),
            # Entity ID registry
            id_registry=dict(initial_state.get("id_registry", {}) or {}),
            id_registry_version=int(initial_state.get("id_registry_version", 1) or 1),
            _id_registry_persisted=dict(initial_state.get("_id_registry_persisted", {}) or {}),
            # Cowriter
            cowriter_session=dict(initial_state.get("cowriter_session", {}) or {}),
            current_options=list(initial_state.get("current_options", []) or []),
            # Planning
            pending_plan=dict(initial_state.get("pending_plan", {}) or {}),
            plan_confirmed=bool(initial_state.get("plan_confirmed", False)),
            planned_steps=list(initial_state.get("planned_steps", []) or []),
            current_step_index=int(initial_state.get("current_step_index", 0) or 0),
            # Recovery
            errors=list(initial_state.get("errors", []) or []),
            retry_count=int(initial_state.get("retry_count", 0) or 0),
            max_retries=int(initial_state.get("max_retries", 3) or 3),
            retry_context=initial_state.get("retry_context"),
            recovery_state=dict(initial_state.get("recovery_state", {}) or {}),
            _model_override=initial_state.get("_model_override", "") or "",
            # Token budget
            budget_total_estimated=int(initial_state.get("budget_total_estimated", 0) or 0),
            budget_model_limit=int(initial_state.get("budget_model_limit", 900_000) or 900_000),
            budget_continuation_count=int(initial_state.get("budget_continuation_count", 0) or 0),
            budget_last_delta=int(initial_state.get("budget_last_delta", 0) or 0),
            budget_warn_level=initial_state.get("budget_warn_level", "") or "",
            # Tool-only loop detection
            tool_only_turns=int(initial_state.get("tool_only_turns", 0) or 0),
            write_only_turns=int(initial_state.get("write_only_turns", 0) or 0),
            # Context compression tracker
            _last_scan_count=int(initial_state.get("_last_scan_count", 0) or 0),
            # Lifecycle
            turn_count=int(initial_state.get("_turn_count", 0) or 0),
            _context_loaded=bool(initial_state.get("_context_loaded", False)),
            _invalidated_sections=set(initial_state.get("_invalidated_sections", []) or []),
            transition="",
        )

    def to_dict(self) -> dict:
        """Serialize to a flat dict for persistence (``save_agent_state``).

        Only real fields are included — no fake LangGraph compatibility
        keys (``current_intent``, ``tool_calls``, ``search_results``) are
        injected.
        """
        return {
            "project_id": self.project_id,
            "user_id": self.user_id,
            "trace_id": self.trace_id,
            "conversation_id": self.conversation_id,
            "project_context": self.project_context,
            "messages": self.messages,
            "mode": self.mode,
            "tool_results": self.tool_results,
            "id_registry": self.id_registry,
            "id_registry_version": self.id_registry_version,
            "active_skills": self.active_skills,
            "intermediate_steps": [],
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "current_options": self.current_options,
            "planned_steps": self.planned_steps,
            "current_step_index": self.current_step_index,
            "errors": self.errors,
            "pending_plan": self.pending_plan,
            "plan_confirmed": self.plan_confirmed,
            "retry_context": self.retry_context,
            "cowriter_session": self.cowriter_session,
            "_context_loaded": self._context_loaded,
            "_invalidated_sections": list(self._invalidated_sections),
            "_turn_count": self.turn_count,
            "recovery_state": self.recovery_state,
            "_model_override": self._model_override,
            "tool_only_turns": self.tool_only_turns,
            "write_only_turns": self.write_only_turns,
            "_last_scan_count": self._last_scan_count,
            # Token budget
            "budget_total_estimated": self.budget_total_estimated,
            "budget_model_limit": self.budget_model_limit,
            "budget_continuation_count": self.budget_continuation_count,
            "budget_last_delta": self.budget_last_delta,
            "budget_warn_level": self.budget_warn_level,
        }
