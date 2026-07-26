from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import AsyncGenerator

from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.guard import InputGuard
from app.agent.memory.conversation import ConversationMemory
from app.agent.memory.history_manager import HistoryManager
from app.api.rate_limiter import get_rate_limiter
from app.llm.client import LLMClient, get_shared_client
from app.llm.types import Message
from app.agent.tools import get_tool_registry as _get_registry
from app.agent.tools import get_tool_descriptions as _build_tool_descriptions
from app.knowledge.skill_engine import _shared_engine as _skill_engine

_CHAT_STREAM_TIMEOUT = 120  # 2 min (was 60s — too tight for writing tools)


class SuperAgent:
    """Orchestrator for the model-driven AI assistant.

    Lifecycle: input guard → conversation setup → history loading →
    attachment injection → autonomous_loop dispatch → state save.

    There is no LangGraph path — the model-driven ``autonomous_loop`` is
    always used.
    """

    def __init__(
        self,
        db: AsyncSession,
        redis_client: Redis | None = None,
        llm_client: LLMClient | None = None,
    ):
        self.db = db
        self.redis_client = redis_client
        self._owns_llm = llm_client is None
        if llm_client is None:
            llm_client = get_shared_client().fork()
        self._llm_client = llm_client

        self.conv_memory = ConversationMemory(redis_client)
        self._history_manager: HistoryManager | None = None
        self.input_guard = InputGuard(rate_limiter=get_rate_limiter())

        # Tool registry — cached for _emit_tool_events write detection
        self._tool_registry_cache: dict | None = None

    @property
    def history_manager(self) -> HistoryManager:
        if self._history_manager is None:
            self._history_manager = HistoryManager(
                redis_client=self.redis_client,
            )
        return self._history_manager

    async def _get_tool_registry(self) -> dict:
        """Lazily load and cache the tool registry for write detection."""
        if self._tool_registry_cache is None:
            self._tool_registry_cache = await asyncio.to_thread(
                _get_registry, llm_client=self._llm_client
            )
        return self._tool_registry_cache

    @staticmethod
    def _extract_changes(params: dict) -> dict:
        skip_keys = {"project_id", "user_id", "conversation_id", "id"}
        changes = {}
        for key, value in params.items():
            if key not in skip_keys and value is not None and value != "":
                changes[key] = str(value)[:200]
        return changes

    async def _emit_tool_events(
        self, final_values: dict, skip_keys: set[str] | None = None
    ):
        """Emit tool_done, plan, and project_updated events.

        No ``option`` event — options are now plain text in the assistant
        response, not structured UI cards.
        """
        tool_results = final_values.get("tool_results", [])
        visible_results = []
        for tr in tool_results:
            if isinstance(tr, dict):
                if tr.get("tool") not in ("cowriter_analysis",):
                    visible_results.append(tr)
            else:
                logger.warning("_emit_tool_events: non-dict in tool_results: type=%s value=%s", type(tr).__name__, str(tr)[:100])
        for tr in visible_results:
            if skip_keys:
                tid = tr.get("_tool_use_id", "")
                if tid and tid in skip_keys:
                    continue
            yield {"type": "tool_done", "data": json.dumps(tr, ensure_ascii=False)}

        # Pending plan (awaiting confirmation)
        raw_pending_plan = final_values.get("pending_plan", {})
        plan_confirmed = final_values.get("plan_confirmed", False)
        if raw_pending_plan and not plan_confirmed:
            steps = raw_pending_plan.get("steps", [])
            extra = {k: v for k, v in raw_pending_plan.items() if k != "steps"}
            yield {
                "type": "plan",
                "data": json.dumps(
                    {"steps": steps, "status": "awaiting_confirmation", **extra},
                    ensure_ascii=False,
                ),
            }

        # Project-updated notification for write tools
        all_tools = await self._get_tool_registry()
        write_tools = []
        tool_details = []
        for tr in visible_results:
            if tr.get("success"):
                tool_name = tr.get("tool", "")
                tool = all_tools.get(tool_name)
                is_write = tool.is_write_operation if tool else False
                if is_write:
                    write_tools.append(tool_name)
                    params = tr.get("params", {})
                    if isinstance(params, dict):
                        changes = self._extract_changes(params)
                    else:
                        changes = {}
                    tool_details.append({"name": tool_name, "changes": changes})

        if write_tools:
            yield {
                "type": "project_updated",
                "data": json.dumps(
                    {
                        "tools_executed": write_tools,
                        "tool_details": tool_details,
                        "all_success": all(
                            tr.get("success", True) for tr in visible_results
                        ),
                    },
                    ensure_ascii=False,
                ),
            }

    async def chat_stream(
        self,
        project_id: str,
        user_id: str,
        message: str,
        conversation_id: str | None = None,
        mode: str = "chat",
        context_view: str | None = None,
        context_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        request_id = str(uuid.uuid4())[:8]
        log = logger.bind(
            request_id=request_id, project_id=project_id, user_id=user_id
        )
        log.info("chat_stream start | mode=%s | msg_len=%d", mode, len(message))

        # 1. Input guard
        guard_error = await self.input_guard.async_check(
            message, rate_limit_key=f"{user_id}:{conversation_id or ''}"
        )
        if guard_error:
            log.warning("guard blocked | reason=%s", guard_error)
            yield {"type": "error", "data": guard_error}
            return

        # 2. Conversation setup
        if not conversation_id:
            conversation_id = await self.conv_memory.create_conversation(
                project_id, user_id
            )
            # Auto-name: use first message (truncated) as title
            title = (message[:60] + '…') if len(message) > 60 else message
            await self.conv_memory.rename_conversation(conversation_id, title)
            log.info("created conversation | conv_id=%s | title=%s", conversation_id, title)

        # 3. History loading
        history = await self.conv_memory.get_history(conversation_id)
        if history:
            # Wire up conversation context for history persistence
            self.history_manager._conversation_id = conversation_id
            self.history_manager._conv_memory = self.conv_memory
            await self.history_manager.restore_last_summary_count()
            history = await self.history_manager.maybe_summarize(history)

        messages = list(history)
        messages.append(Message(role="user", content=message))

        # ── Parse /skillname user command ────────────────────────────
        active_skills: list[str] = []
        parsed_message = message
        if message.startswith("/"):
            skill_query = message[1:].strip()
            skill_data = await _skill_engine.get_skill(skill_query)
            if skill_data is None:
                # Try partial match on display name
                all_skills = await _skill_engine.get_all_skills_meta()
                for s in all_skills:
                    name = s.get("name", "")
                    if skill_query in name:
                        skill_data = await _skill_engine.get_skill(name)
                        if skill_data:
                            break
            if skill_data is None:
                # Try alias match
                for s in await _skill_engine.get_all_skills_meta():
                    aliases = s.get("aliases", []) or []
                    if skill_query in aliases:
                        skill_data = await _skill_engine.get_skill(s.get("name", ""))
                        if skill_data:
                            break
            if skill_data is not None:
                display = skill_data.get("name", skill_query)
                active_skills.append(display)
                parsed_message = f"启用技能「{display}」"
                messages[-1] = Message(role="user", content=parsed_message)
                log.info("user invoked skill via / | skill=%s", display)

        project_context: dict = {}
        if context_view:
            project_context["current_view"] = context_view
        if context_id:
            project_context["current_view_id"] = context_id

        # 4. Load saved agent state
        (
            saved_pending_plan,
            saved_options,
            saved_plan_confirmed,
            saved_mode,
            saved_session,
        ) = await self.conv_memory.load_agent_state(conversation_id)

        # Mode switch: clear stale state
        if saved_mode and saved_mode != mode:
            saved_pending_plan = {}
            saved_options = []
            saved_plan_confirmed = False
            saved_session = {}
            await self.conv_memory.save_agent_state(
                conversation_id, {}, [], False, mode, cowriter_session={}
            )

        # 5. Build initial state dict
        initial_state: dict = {
            "project_id": project_id,
            "user_id": user_id,
            "trace_id": request_id,
            "conversation_id": conversation_id,
            "project_context": project_context,
            "messages": messages,
            "tool_results": [],
            "active_skills": active_skills,
            "mode": mode,
            "intermediate_steps": [],
            "retry_count": 0,
            "max_retries": 3,
            "current_options": saved_options,
            "planned_steps": [],
            "current_step_index": 0,
            "errors": [],
            "pending_plan": saved_pending_plan,
            "plan_confirmed": saved_plan_confirmed,
            "retry_context": None,
            "cowriter_session": saved_session,
            "_context_loaded": False,
            "_turn_count": 1,
            "recovery_state": {},
            "_model_override": "",
        }

        # 6. Save user message
        await self.conv_memory.save_message(
            conversation_id, Message(role="user", content=message)
        )

        yield {"type": "conv_id", "data": conversation_id}

        # 7. Autonomous loop dispatch
        # Per-turn context injection (tool_summary, session_progress, plan_reminder,
        # error_context) is handled dynamically inside autonomous_loop() via
        # AttachmentInjector.build_system_sections() — no pre-loop injection needed.
        assistant_content = ""
        final_values: dict | None = None
        _streaming_tool_results: set[str] = set()

        from app.agent.loop import autonomous_loop

        all_tools = await self._get_tool_registry()
        td = _build_tool_descriptions(all_tools)

        try:
            async for event in autonomous_loop(
                initial_state, all_tools, self._llm_client, self.db, td,
            ):
                if not isinstance(event, dict):
                    continue

                if event.get("_loop_done"):
                    final_values = event["final_state"]
                    break
                elif "_step" in event:
                    yield {"type": "step", "data": event["_step"]}
                elif "_stream_token" in event:
                    token = event["_stream_token"]
                    assistant_content += token
                    yield {"type": "token", "data": token}
                elif "_tool_done" in event:
                    td_val = event["_tool_done"]
                    if not isinstance(td_val, dict):
                        log.warning("_tool_done value is not dict: type=%s value=%s", type(td_val).__name__, str(td_val)[:200])
                        continue
                    yield {
                        "type": "tool_done",
                        "data": json.dumps(td_val, ensure_ascii=False),
                    }
                    _tool_use_id = td_val.get("_tool_use_id", "")
                    if _tool_use_id:
                        _streaming_tool_results.add(_tool_use_id)
                elif "pending_plan" in event:
                    if final_values is None:
                        final_values = {}
                    final_values["pending_plan"] = event["pending_plan"]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            log.error(
                "agent_loop_error | error_type={} | error={}\n{}", type(exc).__name__, exc, tb,
            )
            yield {
                "type": "error",
                "data": json.dumps({"message": f"代理执行出错: {str(exc)[:200]}"}),
            }
            raise

        # 9. Final state fallback
        if final_values is None:
            yield {
                "type": "error",
                "data": json.dumps({"message": "No output produced"}),
            }
            return

        # 10. Emit tool events
        try:
            async for evt in self._emit_tool_events(
                final_values, skip_keys=_streaming_tool_results
            ):
                yield evt
        except Exception as exc:
            log.error("_emit_tool_events failed: {}", exc, exc_info=True)
            yield {"type": "error", "data": json.dumps({"message": f"工具事件处理出错: {str(exc)[:200]}"})}
            raise

        # 11. State cleanup
        plan_was_confirmed = final_values.get("plan_confirmed", False)
        pending_is_empty = not final_values.get("pending_plan", {})
        if plan_was_confirmed and pending_is_empty:
            final_values["current_options"] = []
            final_values["plan_confirmed"] = False
            log.debug("Cleared options/plan_confirmed after successful plan execution")

        # 12. Persist state
        await self.conv_memory.save_agent_state(
            conversation_id,
            final_values.get("pending_plan", {}),
            final_values.get("current_options", []),
            final_values.get("plan_confirmed", False),
            mode=final_values.get("mode", mode),
            cowriter_session=final_values.get("cowriter_session", {}),
        )

        # 13. Fallback: if no tokens were streamed, extract from final state
        if not assistant_content and final_values:
            messages = final_values.get("messages", [])
            if messages:
                last = messages[-1]
                content = (
                    last.content
                    if hasattr(last, "content")
                    else last.get("content", "")
                )
                if content:
                    assistant_content = content
                    yield {"type": "token", "data": content}

        yield {"type": "done", "data": ""}

        if assistant_content:
            await self.conv_memory.save_message(
                conversation_id,
                Message(role="assistant", content=assistant_content),
            )

        log.info("chat_stream done | tokens=%d", len(assistant_content))

    # ── Conversation CRUD ─────────────────────────────────────────────

    async def create_conversation(
        self, project_id: str, user_id: str, title: str = ""
    ) -> str:
        return await self.conv_memory.create_conversation(project_id, user_id, title)

    async def list_conversations(
        self, project_id: str, user_id: str
    ) -> list[dict]:
        return await self.conv_memory.list_conversations(project_id, user_id)

    async def get_conversation(
        self, project_id: str, user_id: str, conversation_id: str
    ) -> dict | None:
        return await self.conv_memory.get_conversation(
            project_id, user_id, conversation_id
        )

    async def delete_conversation(
        self, project_id: str, user_id: str, conversation_id: str
    ) -> bool:
        return await self.conv_memory.delete_conversation(
            project_id, user_id, conversation_id
        )

    # ── Cleanup ───────────────────────────────────────────────────────

    async def close(self) -> None:
        if self.redis_client:
            try:
                await self.redis_client.close()
            except Exception as exc:
                logger.warning("Error closing Redis connection: %s", exc)
        if self._owns_llm and self._llm_client is not None:
            try:
                await self._llm_client.close()
            except Exception as exc:
                logger.warning("Error closing LLM client: %s", exc)


# ── Factory ────────────────────────────────────────────────────────────


def get_super_agent(
    db: AsyncSession, redis: Redis | None = None
) -> SuperAgent:
    return SuperAgent(db, redis)
