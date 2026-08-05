from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AttachmentInjector:
    """Per-turn context injection system.

    Instead of loading ALL context before the first turn, this injects
    relevant context incrementally each turn based on what is actually
    needed.  Inspired by Claude Code's per-turn attachment system.

    ``build_system_sections(state)`` returns a dict of section name →
    content for the system prompt, replacing the inline context building
    that was previously duplicated in loop.py.
    """

    # ── System prompt sections ───────────────────────────────────────

    def build_system_sections(self, state: Any) -> dict[str, str]:
        """Return dict of {section_name: content} for the system prompt.

        Consolidates all per-turn context that was previously built inline
        in loop.py (the ~80 lines building tool_summary, session_text,
        plan_text, error_text).  The caller should append these sections
        to the system prompt.

        Sections returned:
            id_registry, session_progress, plan_reminder, error_context
        """
        sections: dict[str, str] = {}

        # NOTE: tool_summary was intentionally removed (H1).  Tool results are
        # already injected verbatim as role=tool messages in the history, and
        # the id_registry below gives a bounded ID index — a third, condensed
        # copy in the system prompt just re-read the same tokens.

        # Entity ID registry — bounded ID↔label index for tool chaining
        registry = getattr(state, "id_registry", None) or {}
        if isinstance(registry, dict) and registry:
            from app.agent.id_registry import render_id_registry
            rendered = render_id_registry(registry)
            if rendered:
                sections["id_registry"] = rendered

        # Session progress
        session = getattr(state, "cowriter_session", None)
        if hasattr(state, "cowriter_session") and session:
            sess = session or {}
            if sess.get("is_active"):
                phase = sess.get("phase", "explore")
                goal = sess.get("goal", "")
                focus = sess.get("current_focus", "")
                phase_cn = {
                    "explore": "探索",
                    "plan": "计划",
                    "execute": "执行",
                    "review": "评审",
                    "complete": "完成",
                }.get(phase, phase)
                lines = [f"# --- 协作进度 ---\n阶段: {phase_cn}"]
                if goal:
                    lines.append(f"目标: {goal}")
                if focus:
                    lines.append(f"焦点: {focus}")
                sections["session_progress"] = "\n".join(lines)

        # Pending plan reminder
        pending_plan = getattr(state, "pending_plan", None)
        plan_confirmed = getattr(state, "plan_confirmed", False)
        if hasattr(state, "pending_plan") and pending_plan and not plan_confirmed:
            steps = pending_plan.get("steps", [])
            if steps:
                lines = ["# --- 待确认计划 ---"]
                for i, s in enumerate(steps, 1):
                    lines.append(
                        f"  {i}. {s.get('description', s.get('tool', ''))}"
                    )
                lines.append("等待用户确认或拒绝。")
                sections["plan_reminder"] = "\n".join(lines)

        # Error context
        errors = getattr(state, "errors", []) if hasattr(state, "errors") else []
        recent = [e for e in errors[-3:] if e]
        if recent:
            sections["error_context"] = "# --- 最近的错误 ---\n" + "\n".join(
                f"- {e}" for e in recent
            )

        return sections

