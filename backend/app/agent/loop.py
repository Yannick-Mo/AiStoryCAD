"""Autonomous agent loop — model-driven, not code-driven.

A Claude Code-inspired design where the LLM decides: reply, call tools,
ask questions.  Code only handles safety (mode gating, confirmation,
context budget).

Key features:
  - No intent classification — the model sees tools directly and decides.
  - Multi-tool turns: model can chain read → analyze → write in one turn.
  - Streaming tool execution: SAFE tools run as they arrive mid-stream.
  - Interceptor layer: mode gate + confirmation gate.
  - Token-aware context compression.
  - Plan confirm/reject detection at loop entry (no LLM re-generation).
  - Layered error recovery (delegated to recovery.py).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context_compressor import (
    async_compress_context,
)
from app.agent.hooks import hook_registry
from app.agent.interceptors import (
    InterceptResult,
    apply_interceptors,
    build_confirmation_plan,
)
from app.agent.loop_state import LoopState
from app.agent.prompts.builder import get_prompt_builder
from app.agent.token_budget import (
    check_token_budget,
    check_turn_continuation,
    compute_budget,
)
from app.agent.tools import get_filtered_tools, get_tool_descriptions
from app.agent.tools.base import BaseTool
from app.agent.tools.streaming_executor import StreamingToolExecutor
from app.config import settings
from app.llm.client import LLMClient
from app.llm.types import Message
from loguru import logger
from app.knowledge.skill_engine import _shared_engine as _skill_engine

# ── Constants ──────────────────────────────────────────────────────────

MAX_TURNS = 30
MAX_RECOVERY = 3
MAX_RAG_CHARS = 5000
MODEL_CONTEXT_LIMIT = settings.llm_context_window  # tokens (DeepSeek 128K window)

# ── Tool description cache ────────────────────────────────────────────
_TOOL_DESC_CACHE: dict[str, str] = {}

def _get_tool_descriptions_cached(filtered_tools: dict) -> str:
    keys = tuple(sorted(filtered_tools))
    h = str(hash(keys))
    if h not in _TOOL_DESC_CACHE:
        _TOOL_DESC_CACHE[h] = get_tool_descriptions(filtered_tools)
    return _TOOL_DESC_CACHE[h]

# ── Helpers ────────────────────────────────────────────────────────────


def _markdown_to_plain(md: str, max_len: int = 120) -> str:
    """Strip markdown formatting for tool result summaries."""
    md = re.sub(r"#{1,6}\s+", "", md)
    md = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", md)
    md = re.sub(r"`{1,3}[^`]+`{1,3}", "", md)
    md = re.sub(r">\s+", "", md)
    md = re.sub(r"\s+", " ", md).strip()
    return md[:max_len] + ("..." if len(md) > max_len else "")


def _strip_orphan_tool_messages(messages: list[Message]) -> list[Message]:
    """Remove ``tool`` messages whose preceding ``assistant`` lacks ``tool_calls``.

    DeepSeek / OpenAI enforce::

        Messages with role 'tool' must be a response to a preceding
        message with 'tool_calls'.

    When the autonomous loop adds a tool result via a separate turn (plan
    confirm, or the final "no tools" turn), the assistant message may not
    carry ``tool_calls``, which makes the subsequent ``tool`` message
    invalid.  This helper strips those orphaned tool messages so the LLM
    call succeeds.
    """
    cleaned: list[Message] = []
    for m in messages:
        if m.role == "tool":
            # Find the most recent assistant message before this tool
            prev_assistant = None
            for prev in reversed(cleaned):
                if prev.role == "assistant":
                    prev_assistant = prev
                    break
            if prev_assistant is None or not prev_assistant.tool_calls:
                # Orphaned tool message — drop it
                continue
        cleaned.append(m)
    return cleaned


# ── Context invalidation after writes ─────────────────────────────────


def _invalidate_after_write(
    state: LoopState,
    tool_name: str,
    result: dict,
    filtered_tools: dict[str, BaseTool],
) -> LoopState:
    """If *tool_name* is a write operation that succeeded, mark context
    as stale so it's reloaded on the next turn.

    Also tracks which sections were modified (``_invalidated_sections``)
    so the context builder can do targeted cache invalidation.
    """
    if not result.get("success"):
        return state
    tool = filtered_tools.get(tool_name)
    if tool and tool.is_write_operation:
        section = _section_for_tool(tool_name)
        invalidated = set(state._invalidated_sections) | {section}
        return state.replace(
            _context_loaded=False,
            _invalidated_sections=invalidated,
            transition=f"context_invalidated_{section}",
        )
    # Handle invoke_skill — add to active_skills
    if tool_name == "invoke_skill":
        data = result.get("data")
        if isinstance(data, dict):
            skill_name = data.get("skill_name", "")
            if skill_name:
                active = list(state.active_skills)
                if skill_name not in active:
                    active.append(skill_name)
                return state.replace(
                    active_skills=active,
                    _context_loaded=False,
                    transition=f"skill_activated_{skill_name}",
                )
    return state


def _section_for_tool(tool_name: str) -> str:
    """Map a write tool to the context section it modifies."""
    if tool_name in ("update_project", "create_project_from_material"):
        return "project"
    if tool_name in ("create_act", "update_act", "delete_act",
                     "create_chapter", "update_chapter", "delete_chapter",
                     "create_scene", "update_scene", "delete_scene",
                     "set_chapter_goal"):
        return "structure"
    if tool_name in ("write_scene_content", "continue_scene", "rewrite_scene",
                     "expand_selection", "compress_selection"):
        return "content"
    if tool_name in ("create_character", "update_character", "delete_character",
                     "update_relation", "delete_relation"):
        return "characters"
    if tool_name in ("create_edge", "update_edge", "delete_edge"):
        return "edges"
    if tool_name in ("create_theme", "update_theme", "delete_theme",
                     "link_theme_chapter", "unlink_theme_chapter",
                     "set_chapter_rhythm"):
        return "themes"
    return "project"


# ── Event constructors ──────────────────────────────────────────────────


def _event_step(label: str) -> dict:
    return {"_step": label}


def _event_token(text: str) -> dict:
    return {"_stream_token": text}


def _event_tool_done(result: dict) -> dict:
    return {"_tool_done": result}


def _event_plan(plan: dict) -> dict:
    return {"pending_plan": plan}


def _event_done(final_state: dict) -> dict:
    return {"_loop_done": True, "final_state": final_state}


# ── Confirm/reject detection ────────────────────────────────────────────

# Keywords that signal the user wants to confirm a pending plan.
# 匹配方式：消息 strip 空白和标点后必须与某个关键词**完全相等**才算命中
# （见 _detect_plan_decision）。子串匹配会把 "开始写第二章" 误判为确认。
_CONFIRM_KEYWORDS: list[str] = [
    "确认", "执行", "开始", "ok", "yes", "go",
    "没问题", "就这样", "同意", "接受", "做吧", "干吧",
    "按这个来", "照这个来", "用这个", "就按",
    # 全句等值匹配下这些短词是安全的（原 short_confirm_extra）
    "好的", "好", "可以", "行", "直接", "继续",
]
_REJECT_KEYWORDS: list[str] = [
    "拒绝", "取消", "算了", "no", "cancel",
    "换个", "换一个", "重新", "再来",
    "重来", "再想想",
    # 全句等值匹配下这些短词是安全的（原 short_reject_extra）
    "不要", "不行", "不对", "不好", "停",
]

# strip 掉的空白与标点（消息首尾）
_DECISION_STRIP_CHARS = " \t\r\n。！!？?，,~"


def _build_skill_probe_paths(ctx: dict) -> list[str]:
    """Build synthetic file-like paths from project metadata for skill auto-activation.

    Converts the project's genre, title, and logline keywords into path
    strings that ``match_skill_paths_async`` can match against each skill's
    ``paths`` glob patterns.  This bridges the file-path-based conditional
    activation system with the DB-driven project context.
    """
    paths: list[str] = []
    proj_data = ctx.get("project", {})
    genre = proj_data.get("genre", "")
    if genre:
        paths.append(f"genre/{genre}")
        # Also add variant without underscores/dashes for glob matching
        clean_genre = genre.replace("_", "").replace("-", "")
        if clean_genre != genre:
            paths.append(f"genre/{clean_genre}")
    title = proj_data.get("title", "")
    if title:
        for word in re.split(r'[\s,，。、_\-：:()（）]', title):
            word = word.strip().lower()
            if len(word) >= 2:
                paths.append(f"content/{word}")
    logline = proj_data.get("logline", "")
    if logline:
        for word in re.split(r'[\s,，。、_\-：:()（）]', logline):
            word = word.strip().lower()
            if len(word) >= 2:
                paths.append(f"logline/{word}")
    return paths


def _get_recent_scenes_hint(
    context: dict,
    max_scenes: int = 5,
) -> str | None:
    """Build a compact hint of the most recent scene summaries.

    Extracts the last *max_scenes* scenes from the loaded project context
    (ordered by their position in the structure) so the AI has a quick
    reference to recent narrative progress without an extra tool call.
    """
    acts = context.get("acts", [])
    all_scenes: list[dict] = []
    for act in acts:
        for ch in act.get("chapters", []):
            for sc in ch.get("scenes", []):
                all_scenes.append(sc)

    if not all_scenes:
        return None

    # Take the last N scenes (they're already in sort_order)
    recent = all_scenes[-max_scenes:]
    lines = ["\n最近场景快照："]
    for sc in recent:
        title = sc.get("title", "?")
        summary = sc.get("summary", "") or ""
        pov = sc.get("pov_character", "") or ""
        snippet = f"- {title}"
        if pov:
            snippet += f" [{pov}]"
        if summary:
            snippet += f": {summary[:100]}"
        lines.append(snippet)
    return "\n".join(lines)


def _detect_plan_decision(user_content: str, pending_plan: dict) -> str:
    """Check if *user_content* confirms or rejects a pending plan.

    Returns ``"confirm"``, ``"reject"``, or ``""`` (no decision detected).

    匹配规则：消息 strip 空白和标点（。！!？?，,~）后必须**完全等于**
    某个确认/拒绝关键词才算命中。子串或词边界匹配都会造成误判：
    - "开始写第二章" 含确认词 "开始" → 不能当确认，那是新指令
    - "换个开始" 同时含确认词 "开始" 和拒绝词 "换个" → 不能当确认
    带任何其他内容的消息一律返回 ""，交给 LLM 处理。
    """
    if not pending_plan or not pending_plan.get("steps"):
        return ""

    content = user_content.strip(_DECISION_STRIP_CHARS).lower()
    if not content:
        return ""

    if content in (kw.lower() for kw in _CONFIRM_KEYWORDS):
        return "confirm"
    if content in (kw.lower() for kw in _REJECT_KEYWORDS):
        return "reject"
    return ""


# ── System prompt builders ─────────────────────────────────────────────


def _build_chat_system_prompt(sections: list[str]) -> str:
    """System prompt sections from the modular builder (system.yaml)."""
    builder = get_prompt_builder()
    return builder.build(list(sections))


def _render_cowriter_persona() -> str:
    """Return the cowriter persona rendered from cowriter.yaml (single source of truth)."""
    from app.agent.prompts import render_prompt
    result = render_prompt("cowriter")
    return result if result else ""


async def _build_turn_sections(
    state: "LoopState",
    cowriter_persona: str,
    dynamic_sections: dict[str, str],
    filtered_tool_desc: str,
    budget_check: dict,
) -> list[str]:
    """Assemble per-turn system-prompt sections (everything after the static base).

    Returns an ordered list of section strings that the caller joins together
    and appends to the static ``base_system`` prefix.
    """
    from app.agent.response_builder import MODE_DECLARATION_CHAT, MODE_DECLARATION_COWRITER

    proj = state.project_context.get("project", {})
    sections: list[str] = []

    # 1. Mode declaration
    sections.append(MODE_DECLARATION_CHAT if state.mode == "chat" else MODE_DECLARATION_COWRITER)

    # 2. Cowriter persona
    if cowriter_persona:
        sections.append(cowriter_persona)

    # 3. Project context header
    proj_title = proj.get("title", "未命名")
    proj_genre = proj.get("genre", "")
    proj_id = state.project_id or proj.get("id", "unknown")
    proj_logline = proj.get("logline", "")
    proj_global_settings = proj.get("global_settings", "")

    ctx_parts = [f"# --- 当前项目 ---\n项目: {proj_title}"]
    if proj_genre:
        ctx_parts.append(f"类型: {proj_genre}")
    if proj_logline:
        ctx_parts.append(f"一句话梗概: {proj_logline}")
    if proj_global_settings:
        if len(proj_global_settings) > 2000:
            gs_snippet = proj_global_settings[:2000] + f"\n... [全文共 {len(proj_global_settings)} 字，已截断。可用 read_project 读取完整设定]"
        else:
            gs_snippet = proj_global_settings
        ctx_parts.append(f"全局设定:\n{gs_snippet}")
    ctx_parts.append(f"Project ID: {proj_id}")
    sections.append("\n".join(ctx_parts))

    # 4. Project stats
    acts_data = state.project_context.get("acts", [])
    characters_data = state.project_context.get("characters", [])
    themes_data = state.project_context.get("themes", [])
    relations_data = state.project_context.get("relations", [])
    edges_data = state.project_context.get("edges", [])
    total_ch = sum(len(a.get("chapters", [])) for a in acts_data)
    total_sc = sum(
        sum(len(ch.get("scenes", [])) for ch in a.get("chapters", []))
        for a in acts_data
    )
    stats_parts = [f"项目规模：{len(acts_data)}幕/{total_ch}章/{total_sc}场"]
    if characters_data:
        stats_parts.append(f"{len(characters_data)}角色")
    if themes_data:
        stats_parts.append(f"{len(themes_data)}主题")
    if relations_data:
        stats_parts.append(f"{len(relations_data)}条关系")
    if edges_data:
        stats_parts.append(f"{len(edges_data)}条连线")
    sections.append(" | ".join(stats_parts))

    # 5. Data access guidance
    sections.append(
        "项目框架数据已在上下文中提供（结构树、角色档案、主题、关系）。\n"
        "场景正文需通过 read_scene 工具读取。如需更多细节，使用 read_character / read_chapter / list_relations 等工具。"
    )

    # 6. Recent scenes hint
    recent_hint = state.project_context.get("_recent_scenes_hint")
    if recent_hint:
        sections.append(recent_hint)

    # 7. Available skills
    available_skills = state.project_context.get("available_skills", [])
    if available_skills:
        skill_lines = ["\n# --- 可用写作技能（AI 可主动调用） ---"]
        for s in available_skills:
            name = s.get("name", "?")
            desc = s.get("description", "")
            when = s.get("when_to_use", "")
            if when:
                skill_lines.append(f"- {name}: {desc}")
                skill_lines.append(f"  适用场景：{when[:200]}")
            else:
                skill_lines.append(f"- {name}: {desc}")
        skill_lines.append("")
        skill_lines.append("你可以通过调用 invoke_skill 工具来启用某个技能。启用后其专属提示词将生效，在创作模式下还会解锁专属工具。")
        skill_lines.append("用户也可在消息中以 /技能名称 的形式直接调用。")
        sections.append("\n".join(skill_lines))

    # 8. Active skill prompts
    active_skills = state.active_skills or []
    if active_skills and state.project_id:
        try:
            merged_prompts = await _skill_engine.get_merged_prompts(active_skills)
            if merged_prompts:
                prompt_lines = ["\n# --- 当前已激活技能写作指导 ---"]
                for key, val in merged_prompts.items():
                    prompt_lines.append(f"\n## {key}\n{val.strip()}")
                sections.append("\n".join(prompt_lines))
        except Exception:
            logger.warning("Failed to load active skill prompts", exc_info=True)

    # 9. Dynamic sections from AttachmentInjector
    for section_name in ("tool_summary", "session_progress", "plan_reminder", "error_context"):
        text = dynamic_sections.get(section_name)
        if text:
            sections.append(text)

    # 10. Token budget warning
    if state.budget_warn_level:
        sections.append(budget_check["message"])

    # 11. Tool list
    sections.append(f"# --- 可用工具 ---\n{filtered_tool_desc}")

    return sections


def _build_tool_schemas(filtered_tools: dict[str, BaseTool]) -> list:
    """Convert filtered tools to ToolDef list for the LLM client."""
    from app.llm.types import ToolDef
    schemas = []
    for t in filtered_tools.values():
        d = t.to_openai_tool()
        schemas.append(ToolDef(function=d.get("function", {})))
    return schemas


# ── Main Autonomous Loop ────────────────────────────────────────────────


async def autonomous_loop(
    initial_state: dict,
    tools: dict[str, BaseTool],
    llm_client: LLMClient,
    db: AsyncSession,
    tool_descriptions: str,
) -> AsyncGenerator[dict, None]:
    """Model-driven autonomous agent loop.

    The LLM decides what to do each turn — reply, call tools, ask for
    direction.  Code only handles safety (mode gating, confirmation,
    context budget).
    """

    # ── State initialization ────────────────────────────────────────
    state = LoopState.from_initial(initial_state)
    llm = llm_client

    # ── Attachment injector (for per-turn context sections) ────────
    from app.agent.attachments import AttachmentInjector
    _attach = AttachmentInjector()

    # ── Build static system prompt (once, outside loop) ─────────────
    base_sections = ["identity", "output_style", "tool_usage",
                     "writing_advice", "prohibited_behaviors", "style_guide"]
    if state.mode == "chat":
        base_sections.append("chat_mode_restrictions")

    base_system = _build_chat_system_prompt(base_sections)
    from app.agent.knowledge import APP_GUIDE
    base_system += "\n\n# --- 应用参考 ---\n" + APP_GUIDE
    base_system += """
# --- 项目数据访问规则（必须遵守） ---
- 项目框架数据（幕/章/场景结构、角色档案、主题、关系）已在上下文中提供，可直接引用。
- 场景正文内容不包含在上下文中——需要使用 read_scene 工具读取。
- 在进行写入操作之前，先调用 read 工具获取最新数据。
- 不要编造角色、章节、场景或关系数据。
"""
    cowriter_persona = _render_cowriter_persona() if state.mode == "cowriter" else ""

    # ── 循环外初始化（最终生成阶段会用到）────────────────────────────
    # 计划确认分支会在循环体前段 break（跳过本轮的流式赋值），
    # 这两个变量必须在循环外先绑定，否则最终生成阶段抛 NameError。
    assistant_text_parts: list[str] = []
    active_model = state._model_override or None

    # ── Main Loop ────────────────────────────────────────────────────
    while state.turn_count < MAX_TURNS:
        turn_start = time.monotonic()
        state = state.replace(turn_count=state.turn_count + 1)
        logger.info(
            "autonomous_loop turn=%d | mode=%s | msgs=%d | transition=%s",
            state.turn_count, state.mode, len(state.messages), state.transition,
        )

        # ── Phase 0: Context loading (runs on first turn AND after writes) ──
        if not state._context_loaded and state.project_id:
            yield _event_step("加载项目数据...")
            try:
                from app.agent.context import ContextBuilder
                import uuid as _uuid

                builder = ContextBuilder(db)
                query_hint = ""
                if state.messages:
                    last = state.messages[-1]
                    query_hint = (last.content or "")[:200]

                skip_ctx_cache = len(state._invalidated_sections) > 0

                ctx = await builder.build_summary(
                    _uuid.UUID(state.project_id),
                    query_hint=query_hint,
                    depth="framework",
                    skip_cache=skip_ctx_cache,
                )

                # Prepend recent scene summaries hint
                recent_hint = _get_recent_scenes_hint(ctx)
                if recent_hint:
                    ctx["_recent_scenes_hint"] = recent_hint

                state = state.replace(project_context=ctx, _context_loaded=True,
                                      _invalidated_sections=set(),
                                      transition=f"context_loaded_framework")

                # ── Auto-activate skills by genre/title keyword ──
                try:
                    probe_paths = _build_skill_probe_paths(ctx)
                    if probe_paths:
                        matched = await _skill_engine.match_skill_paths_async(probe_paths)
                        if matched:
                            new_skills = list(state.active_skills)
                            for name in matched:
                                if name not in new_skills:
                                    new_skills.append(name)
                            if len(new_skills) != len(state.active_skills):
                                state = state.replace(
                                    active_skills=new_skills,
                                    transition=f"skills_auto_activated_{len(matched)}",
                                )
                                logger.info("Auto-activated skills by genre: %s", matched)
                except Exception:
                    logger.warning("Skill auto-activation failed", exc_info=True)
            except Exception as e:
                logger.warning("Context load skipped/partial: %s", e)
                await db.rollback()
                state = state.replace(transition="context_load_failed", _context_loaded=True)

        # ── Re-filter tools (skills may have changed) ──────────────
        filtered_tools = get_filtered_tools(tools, mode=state.mode)
        filtered_tool_desc = _get_tool_descriptions_cached(filtered_tools) or tool_descriptions
        tool_schemas = _build_tool_schemas(filtered_tools)

        # ── Token budget check ─────────────────────────────────────
        budget = compute_budget(state.messages, state.tool_results, MODEL_CONTEXT_LIMIT)
        budget_check = check_token_budget(budget)
        state = state.replace(
            budget_total_estimated=budget.total_estimated_tokens,
            budget_model_limit=budget.model_limit,
            budget_warn_level=budget_check["warn"],
            budget_last_delta=budget.total_estimated_tokens - state.budget_total_estimated,
        )

        # ── Confirm/reject detection (before LLM call) ────────────
        if state.pending_plan and not state.plan_confirmed:
            last_user_msg = ""
            for m in reversed(state.messages):
                if m.role == "user":
                    last_user_msg = m.content or ""
                    break

            decision = _detect_plan_decision(last_user_msg, state.pending_plan)
            if decision == "confirm":
                logger.info("Plan confirmed by user — executing plan steps directly")
                yield _event_step("执行已确认的计划...")

                # Build a synthetic assistant message with tool_calls from
                # the pending_plan so the tool-result messages that follow
                # have a valid preceding tool_calls message (OpenAI API req).
                from app.llm.types import ToolCall
                plan_tool_calls = []
                for step in state.pending_plan.get("steps", []):
                    tc = ToolCall(
                        id=step.get("tool_use_id", f"plan_{step.get('tool', '')}"),
                        function={"name": step.get("tool", ""), "arguments": json.dumps(step.get("params", {}))},
                    )
                    plan_tool_calls.append(tc)

                state = state.replace(
                    messages=state.messages + [
                        Message(role="assistant", content=None, tool_calls=plan_tool_calls)
                    ],
                )

                for step in state.pending_plan.get("steps", []):
                    tool_name = step.get("tool", "")
                    args = step.get("params", {})
                    tool_use_id = step.get("tool_use_id", "")
                    try:
                        result = await StreamingToolExecutor(
                            filtered_tools, db,
                            project_id=state.project_id,
                            user_id=state.user_id,
                        ).execute_tool(tool_name, args, tool_use_id)
                    except Exception as exc:
                        result = {"tool": tool_name, "success": False, "error": str(exc)}
                    yield _event_tool_done(result)
                    state = _invalidate_after_write(state, tool_name, result, filtered_tools)

                    # Build tool result message
                    if result.get("success"):
                        data = result.get("data", "")
                        content = f"[工具执行结果: {tool_name}]\n{data}"
                    else:
                        err_text = result.get('error', 'unknown')
                        hint = result.get('correction_hint', '')
                        content = f"[工具执行失败: {tool_name}]\n错误: {err_text[:1000]}"
                        if hint:
                            content += f"\n修正提示: {hint}"

                    new_tr = list(state.tool_results) + [result]
                    state = state.replace(
                        messages=state.messages + [Message(role="tool", content=content, tool_call_id=tool_use_id)],
                        tool_results=new_tr,
                    )

                state = state.replace(
                    pending_plan={},
                    plan_confirmed=True,
                    transition="plan_confirmed_and_executed",
                )
                break  # Skip LLM call, go to final response

            elif decision == "reject":
                logger.info("Plan rejected by user — clearing pending plan")
                state = state.replace(
                    pending_plan={},
                    plan_confirmed=False,
                    transition="plan_rejected",
                )
                yield _event_step("已取消计划...")
                # Fall through to LLM for response about rejection

        # ── Step 1: Context Management (proactive, with auto-escalation) ──
        original_count = len(state.messages)
        if abs(original_count - state._last_scan_count) < 3:
            compressed = list(state.messages)
        else:
            compressed = await async_compress_context(state.messages, llm.chat, model_limit=MODEL_CONTEXT_LIMIT)
            state = state.replace(_last_scan_count=original_count)
        if len(compressed) != original_count:
            reactive = len(compressed) < original_count * 0.3
            state = state.replace(
                messages=compressed,
                transition="reactive_compressed" if reactive else "context_compressed",
            )
            logger.info("Compressed %d → %d messages", original_count, len(state.messages))

        # ── Step 2: Build messages for LLM ─────────────────────────
        dynamic_sections = _attach.build_system_sections(state)
        sections = await _build_turn_sections(
            state, cowriter_persona, dynamic_sections,
            filtered_tool_desc, budget_check,
        )
        system_content = base_system + "\n\n" + "\n\n".join(sections)

        # Build final messages — strip orphaned tool messages that would
        # violate the OpenAI/DeepSeek API requirement (tool must follow
        # an assistant with tool_calls).
        gen_msgs = [Message(role="system", content=system_content)]
        gen_msgs.extend(_strip_orphan_tool_messages(list(state.messages)))

        yield _event_step("思考中...")

        # ── Step 3: LLM streaming + tool execution ─────────────────
        streaming_executor = StreamingToolExecutor(
            filtered_tools, db,
            project_id=state.project_id,
            user_id=state.user_id,
        )
        tool_blocks: list[tuple[str, dict, str]] = []
        # Preserve the original ToolCall objects so the assistant message
        # carries a valid tool_calls field (required by OpenAI/DeepSeek API).
        tool_call_objects: list = []
        # tool_use_ids whose tool_done event was already emitted mid-stream —
        # await_pending_safe() returns ALL results (for message bookkeeping),
        # so the frontend event must be deduplicated here.
        emitted_tool_ids: set[str] = set()
        assistant_text_parts: list[str] = []
        assistant_reasoning_parts: list[str] = []
        tool_use_count = 0

        # Resolve model override (set by recovery model switch)
        active_model = state._model_override or None

        try:
            async for chunk in llm.chat_stream_with_tools(
                messages=gen_msgs,
                tools=tool_schemas,
                temperature=0.7,
                request_id=state.trace_id,
                model=active_model,
            ):
                if chunk.content:
                    assistant_text_parts.append(chunk.content)
                    yield _event_token(chunk.content)

                if chunk.tool_call:
                    tc = chunk.tool_call
                    fn = tc.function if hasattr(tc, "function") else {}
                    name = fn.get("name", "") if isinstance(fn, dict) else getattr(fn, "name", "")
                    args_raw = fn.get("arguments", "{}") if isinstance(fn, dict) else getattr(fn, "arguments", "{}")

                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw)
                        except json.JSONDecodeError:
                            args = {}
                    elif isinstance(args_raw, dict):
                        args = args_raw
                    else:
                        args = {}

                    if name and name.strip():
                        tool_use_count += 1
                        tool_use_id = getattr(tc, "id", f"call_{tool_use_count}")
                        tool_blocks.append((name.strip(), args, tool_use_id))
                        tool_call_objects.append(tc)
                        streaming_executor.add_tool(tc, tool_use_id=tool_use_id)

                if chunk.reasoning_content:
                    assistant_reasoning_parts.append(chunk.reasoning_content)

                for result in streaming_executor.get_completed_results():
                    yield _event_tool_done(result)
                    _tid = result.get("_tool_use_id", "")
                    if _tid:
                        emitted_tool_ids.add(_tid)

                if chunk.finish_reason:
                    logger.debug("Stream finish: %s", chunk.finish_reason)

        except asyncio.CancelledError:
            streaming_executor.discard()
            raise
        except Exception as e:
            logger.error("LLM streaming error: {}", e, exc_info=True)
            streaming_executor.discard()
            # Run post-error hooks
            await hook_registry.run("post_error", state=state, llm_client=llm, error=str(e),
                                     turn_start=turn_start)
            # --- Rollback any poisoned transactions from tool errors ---
            # When a tool's run() raises (e.g. duplicate character), asyncpg
            # marks the connection as aborted.  Without a rollback here, ALL
            # subsequent reads fail with InFailedSQLTransactionError.
            try:
                await db.rollback()
            except Exception:
                pass
            # ── Reactive compression for 413 / context overflow ──
            error_lower = str(e).lower()
            if any(kw in error_lower for kw in ("context length", "too long", "413", "token limit", "prompt too long")):
                from app.agent.context_compressor import reactive_compress
                compressed = reactive_compress(state.messages, model_limit=MODEL_CONTEXT_LIMIT)
                state = state.replace(
                    messages=compressed,
                    retry_count=state.retry_count + 1,
                    transition="reactive_compressed",
                )
                await hook_registry.run("post_turn", state=state, llm_client=llm,
                                         turn_start=turn_start)
                continue
            # --- Recover ---
            decision = await _try_recovery(state, llm, str(e))
            if decision.get("give_up"):
                assistant_text = "".join(assistant_text_parts)
                if assistant_text:
                    state = state.replace(
                        messages=state.messages + [Message(role="assistant", content=assistant_text)],
                        transition="error_give_up",
                    )
                yield _event_token(f"\n\n[发生错误: {decision.get('message', str(e))}]")
                await hook_registry.run("post_turn", state=state, llm_client=llm,
                                         turn_start=turn_start)
                break
            else:
                state = decision.get("state", state)
                new_errors = (state.errors + [str(e)])[-20:]
                state = state.replace(
                    errors=new_errors,
                    transition="error_recovery_retry",
                )
                delay = decision.get("delay_seconds", 0)
                if delay > 0:
                    await asyncio.sleep(delay)
                await hook_registry.run("post_turn", state=state, llm_client=llm,
                                         turn_start=turn_start)
                continue

        # ── Step 4: Await in-flight SAFE tools only ────────────────
        # await_pending_safe() returns ALL SAFE results from this round,
        # including those already emitted to the frontend mid-stream — every
        # tool_call_id must land in safe_result_map so Step 6c can build the
        # mandatory role=tool message (API rejects orphan tool_calls).
        safe_results = await streaming_executor.await_pending_safe()
        queued_excl, queued_barrier = streaming_executor.get_queued_tools()

        new_tool_results = list(state.tool_results)
        safe_result_map: dict[str, dict] = {}
        for r in safe_results:
            if r.get("tool") not in ("cowriter_analysis",):
                new_tool_results.append(r)
                if r.get("_tool_use_id", "") not in emitted_tool_ids:
                    yield _event_tool_done(r)
                safe_result_map[r.get("_tool_use_id", "")] = r
        state = state.replace(tool_results=new_tool_results)

        # ── Step 5: Build assistant message (WITH tool_calls & reasoning for API spec) ──
        assistant_text = "".join(assistant_text_parts)
        reasoning_text = "".join(assistant_reasoning_parts) or None
        if assistant_text.strip() or tool_call_objects:
            assistant_msg = Message(
                role="assistant",
                content=assistant_text or None,
                reasoning_content=reasoning_text,
            )
            if tool_call_objects:
                assistant_msg.tool_calls = list(tool_call_objects)
            state = state.replace(
                messages=state.messages + [assistant_msg],
            )


        # ── Step 6: Interceptor Layer (BEFORE EXCL/BARRIER exec) ───
        if tool_blocks:
            intercept: InterceptResult = apply_interceptors(
                tool_blocks,
                mode=state.mode,
                cowriter_session=state.cowriter_session,
                tools_registry=filtered_tools,
            )

            queued_ids: set[str] = {
                tid for _, _, tid in queued_excl + queued_barrier
            }

            # 6a: Mode-gated blocks — feed back as tool errors, continue loop
            if intercept.blocked:
                streaming_executor.clear_queued()
                for tool_name, args, tool_use_id in tool_blocks:
                    if tool_name in intercept.blocked_tools:
                        logger.warning("Tool '{}' blocked in chat mode", tool_name)
                        result = {
                            "tool": tool_name,
                            "success": False,
                            "error": f"对话模式禁止写入操作，工具已被拦截",
                        }
                        new_tool_results.append(result)
                        yield _event_tool_done(result)
                        content = (
                            f"[工具执行失败: {tool_name}]\n"
                            f"对话模式禁止写入操作，工具已被拦截。请向用户说明需要切换到协作模式来完成写入操作。"
                        )
                        state = state.replace(
                            messages=state.messages
                            + [Message(role="tool", content=content, tool_call_id=tool_use_id)],
                            tool_results=new_tool_results,
                            transition="tool_blocked_continue",
                        )
                # Fall through to execute allowed tools if any

            # 6b: Confirmation needed
            if intercept.needs_confirmation:
                streaming_executor.clear_queued()
                plan = build_confirmation_plan(intercept.pending_tools, filtered_tools)
                state = state.replace(
                    pending_plan=plan,
                    plan_confirmed=False,
                    transition="plan_generated_for_confirmation",
                )
                yield _event_plan(plan)
                yield _event_step("等待确认...")
                break

            # 6c: Allowed tools — execute queued (non-SAFE) ones
            for tool_name, args, tool_use_id in intercept.allowed_tools:
                if tool_use_id in queued_ids:
                    try:
                        result = await streaming_executor.execute_tool(tool_name, args, tool_use_id)
                    except Exception as tool_err:
                        result = {"tool": tool_name, "success": False, "error": str(tool_err)}
                        logger.error("Tool %s failed: %s", tool_name, tool_err)

                    new_tool_results.append(result)
                    yield _event_tool_done(result)
                    state = _invalidate_after_write(state, tool_name, result, filtered_tools)

                    if result.get("success"):
                        data = result.get("data", "")
                        content = f"[工具执行结果: {tool_name}]\n{data}"
                    else:
                        err_text = result.get('error', 'unknown')
                        hint = result.get('correction_hint', '')
                        content = f"[工具执行失败: {tool_name}]\n错误: {err_text[:1000]}"
                        if hint:
                            content += f"\n修正提示: {hint}"

                    state = state.replace(
                        messages=state.messages + [Message(role="tool", content=content, tool_call_id=tool_use_id)],
                        tool_results=new_tool_results,
                    )
                else:
                    # SAFE tool — build message for LLM next turn
                    existing = safe_result_map.get(tool_use_id)
                    if existing:
                        state = _invalidate_after_write(state, tool_name, existing, filtered_tools)
                        if existing.get("success"):
                            data = existing.get("data", "")
                            content = f"[工具执行结果: {tool_name}]\n{data}"
                        else:
                            err_text = existing.get('error', 'unknown')
                            hint = existing.get('correction_hint', '')
                            content = f"[工具执行失败: {tool_name}]\n错误: {err_text[:1000]}"
                            if hint:
                                content += f"\n修正提示: {hint}"
                        state = state.replace(
                            messages=state.messages + [Message(role="tool", content=content, tool_call_id=tool_use_id)],
                        )

            streaming_executor.clear_queued()

        # ── Post-turn hooks (runs before any break/continue decision) ──
        await hook_registry.run("post_turn", state=state, llm_client=llm,
                                 turn_start=turn_start)

        # ── Step 7: Decide next ─────────────────────────────────────
        if not tool_blocks:
            logger.debug("No tool calls — finishing turn")
            break

        if state.transition == "plan_generated_for_confirmation":
            break

        # ── Step 7a: Detect tool-parameter failure cascade ──────────
        # If 3+ tools failed with "参数缺失" in this turn, inject a
        # system reminder that breaks the retry loop. This is critical
        # for DeepSeek flash models which tend to ignore error feedback.
        missing_param_failures = 0
        for r in new_tool_results:
            err = r.get("error")
            if err is None:
                continue
            if not isinstance(err, str):
                err = str(err)
            if "参数缺失" in err or "未提供" in err:
                missing_param_failures += 1
        if missing_param_failures >= 3:
            reminder = (
                "⚠️ 本轮有 {} 个工具因为缺少必要参数而调用失败。\n"
                "请停止逐一尝试每个工具！先调用 list_* 系列工具（list_chapters、"
                "list_scenes、list_characters）获取有效的 ID，然后再用这些 ID "
                "调用需要它们的工具。\n"
                "工具列表中每个工具后标注了 (必须: ...) —— 这表示该参数必须提供。"
            ).format(missing_param_failures)
            state = state.replace(
                messages=state.messages + [Message(role="system", content=reminder)],
                transition="param_missing_warning",
            )

        # Tools executed — continue for chained operations
        logger.info("Tools executed, continuing loop for chained operations")
        state = state.replace(
            retry_count=0,
            transition="tool_executed_continue",
        )

        if not assistant_text.strip():
            # Don't count turns where a write tool succeeded — DeepSeek
            # models often produce tool-only calls after writes and the
            # context-invalidation chain (write → invalidate → reload → write)
            # is legitimate.  Reset the counter so long write chains work.
            had_successful_write = any(
                r.get("success") and
                filtered_tools.get(r.get("tool")) is not None and
                filtered_tools[r.get("tool")].is_write_operation
                for r in new_tool_results
            )
            if had_successful_write:
                state = state.replace(tool_only_turns=0)
                # Track write-only turns separately to catch infinite write loops
                state = state.replace(write_only_turns=state.write_only_turns + 1)
                if state.write_only_turns > 6:
                    logger.warning("Write-only loop detected — breaking")
                    yield _event_token("\n\n[连续写入操作已超限，请停止写入并检查项目数据]")
                    break
            else:
                state = state.replace(tool_only_turns=state.tool_only_turns + 1, write_only_turns=0)
                if state.tool_only_turns > 6:
                    logger.warning("Tool-only loop detected — breaking")
                    yield _event_token("\n\n[连续工具调用已超限，请重新描述你的需求]")
                    break
        else:
            state = state.replace(tool_only_turns=0)

    # ── Final: Generate response ────────────────────────────────────
    yield _event_step("生成回复...")

    last_msg = state.messages[-1] if state.messages else None
    if last_msg and last_msg.role == "assistant":
        pass  # Already have a response
    else:
        from app.agent.response_builder import build_system_prompt

        gen_state = state.to_dict()
        try:
            sys_content = await build_system_prompt(gen_state)
        except Exception as e:
            logger.warning("build_system_prompt failed: %s", e)
            # Build a reasonable fallback in Chinese
            proj_name = gen_state.get("project_context", {}).get("project", {}).get("title", "")
            from app.agent.response_builder import MODE_DECLARATION_CHAT, MODE_DECLARATION_COWRITER
            mode_decl = MODE_DECLARATION_COWRITER if gen_state.get("mode") == "cowriter" else MODE_DECLARATION_CHAT
            sys_content = (
                f"你是 StoryCAD AI，一位经验丰富的中文小说编辑和创作助手。\n"
                f"当前项目：{proj_name or '未命名'}\n"
                f"{mode_decl}\n"
                f"请根据对话历史，用中文回复用户。"
            )

        # Strip orphaned tool messages that lack a preceding assistant
        # with tool_calls (DeepSeek/OpenAI API rejects these).
        msgs_for_final = list(state.messages)
        cleaned = _strip_orphan_tool_messages(msgs_for_final)

        gen_msgs_final = [Message(role="system", content=sys_content)]
        gen_msgs_final.extend(cleaned)

        try:
            async for token in llm.chat_stream_tokens(
                messages=gen_msgs_final,
                temperature=0.7,
                request_id=state.trace_id,
                model=active_model,
            ):
                yield _event_token(token)
        except Exception as e:
            logger.error("Final generate error: %s", e, exc_info=True)
            err_msg = str(e)
            # If we already had assistant text from streaming, don't overwrite
            if not assistant_text_parts:
                yield _event_token(f"\n\n[生成回复时出错: {err_msg[:200]}]")
            else:
                logger.warning("Final gen failed but partial response exists")

    # ── Build final state dict ──────────────────────────────────────
    final_state = state.to_dict()
    yield _event_done(final_state)


# ── Recovery helper ─────────────────────────────────────────────────────


async def _try_recovery(state: LoopState, llm: LLMClient, error: str) -> dict:
    """Attempt error recovery. Delegates to ``RecoveryExecutor``.

    Returns:
        ``{"give_up": False, "state": <updated LoopState>}`` if recovery
        was applied and we should retry.
        ``{"give_up": True, "message": "..."}`` if recovery is exhausted.
    """
    from app.agent.recovery import ErrorClassifier, RecoveryAction, RecoveryExecutor, get_fallback_models

    decision = ErrorClassifier.classify(
        error,
        state.retry_count,
        max_retries=MAX_RECOVERY,
        recovery_history=state.recovery_state.get("recovery_history", []),
    )

    logger.info(
        "Recovery: action=%s attempt=%d error=%s",
        decision.action.value, state.retry_count, error[:100],
    )

    if decision.action == RecoveryAction.GIVE_UP:
        return {"give_up": True, "message": decision.message}

    executor = RecoveryExecutor(
        fallback_models=get_fallback_models(),
        recovery_state=state.recovery_state,
    )
    updates = await executor.apply(decision, state.to_dict())
    state_dict = state.to_dict()
    state_dict.update(updates)
    retry_state = LoopState.from_initial(state_dict).replace(
        retry_count=state.retry_count + 1,
    )

    transition_map = {
        RecoveryAction.RETRY: "recovery_retry",
        RecoveryAction.RETRY_WITH_ERROR_CONTEXT: "recovery_error_context",
        RecoveryAction.RETRY_WITH_COMPRESSED_CONTEXT: "recovery_compressed",
        RecoveryAction.RETRY_ESCALATED_TOKENS: "recovery_escalated",
        RecoveryAction.SWITCH_MODEL: "recovery_model_switch",
    }
    transition = transition_map.get(decision.action, "recovery_unknown")

    return {
        "give_up": False,
        "state": retry_state.replace(transition=transition),
        "delay_seconds": decision.delay_seconds,
    }
