# backend/app/agent/tools/streaming_executor.py
# ============================================================================
# StreamingToolExecutor — executes tools as they arrive from a streaming LLM
# response, rather than waiting for the full response.
#
# Inspired by Claude Code's StreamingToolExecutor in query.ts:
#   - Read-only (SAFE) tools execute immediately in parallel.
#   - Write (EXCLUSIVE) tools queue and execute serially after SAFE tools
#     complete.
# ============================================================================
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tools whose output is typically very long and should be summarized.
_ANALYSIS_TOOL_NAMES: set[str] = {
    "analyze_chapter", "project_health", "check_consistency",
    "analyze_rhythm", "suggest_next",
}

# Consistency reports are the one analysis output the LLM still needs to act
# on in detail; give them a larger budget than the generic 2000 chars.
_ANALYSIS_TOOL_QUOTAS: dict[str, int] = {
    "check_consistency": 6000,
}

# List/structural tools whose output the LLM uses for ID lookup.
# The LLM needs ALL items (not just first N) to match user intent to IDs.
# Per-item long text fields will be truncated to keep total size down.
_LIST_TOOL_NAMES: set[str] = {
    "list_chapters", "list_scenes", "list_characters",
    "list_relations", "list_edges", "read_full_project",
}

# Generic list/structural tools — the LLM mostly needs IDs and names,
# not the full content of each entity.
_STRUCTURAL_TOOL_NAMES: set[str] = {
    "list_chapters", "list_scenes", "list_characters",
    "list_relations", "list_edges", "search_nodes",
}


def _smart_summarise(data: str, max_chars: int, tool_name: str) -> str:
    """Structure-aware summarization instead of blind truncation.

    Three strategies:
    1. **JSON list** — for list tools: keep ALL items, truncate per-item fields;
       for other tools: keep first 3 items
    2. **JSON dict** — truncate long string values and long arrays
    3. **Long text** — head + tail with middle compressed
    """
    if len(data) <= max_chars:
        return data

    if data.strip().startswith(("{", "[")):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, list):
                return _summarise_json_list(parsed, max_chars, tool_name)
            if isinstance(parsed, dict):
                return _summarise_json_dict(parsed, max_chars, data, tool_name)
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 3: Head + tail for long text
    half = max_chars // 2
    head = data[:half]
    tail = data[-half:] if len(data) > half * 2 else ""
    if tail:
        return f"{head}\n\n...[中间省略 {len(data) - max_chars} 字符]...\n\n{tail}"
    return data[:max_chars] + f"\n... [truncated, {len(data)} chars total]"


def _summarise_json_list(parsed: list, max_chars: int, tool_name: str) -> str:
    """Summarise a JSON list, keeping ALL items for ID-lookup tools."""
    total = len(parsed)
    if total <= 3:
        return json.dumps(parsed, ensure_ascii=False, indent=2)[:max_chars]

    if tool_name in _LIST_TOOL_NAMES:
        # Keep ALL items, truncate long fields per item
        compressed = []
        for item in parsed:
            if isinstance(item, dict):
                c = {}
                for k, v in item.items():
                    if isinstance(v, str) and len(v) > 120:
                        c[k] = v[:80] + f"... [{len(v)} chars]"
                    else:
                        c[k] = v
                compressed.append(c)
            else:
                compressed.append(item)
        result = json.dumps(compressed, ensure_ascii=False, indent=2)
        if len(result) > max_chars:
            # Even with per-field truncation, too large — drop verbose fields
            verbose_keys = {
                "summary", "description", "notes", "setting", "content",
                "motivation", "background", "personality", "appearance",
                "outline", "rhythm", "beat", "goal", "style",
            }
            compressed = []
            for item in parsed:
                if isinstance(item, dict):
                    c = {}
                    for k, v in item.items():
                        if k in verbose_keys and isinstance(v, str) and len(v) > 50:
                            c[k] = v[:30] + f"... [{len(v)} chars]"
                        else:
                            c[k] = v
                    compressed.append(c)
                else:
                    compressed.append(item)
            result = json.dumps(compressed, ensure_ascii=False, indent=2)
            if len(result) > max_chars:
                full_len = len(result)
                result = result[:max_chars] + f"\n... [truncated, {full_len} chars total]"
        return result

    # Non-list tool: keep first 3 items
    kept = json.dumps(parsed[:3], ensure_ascii=False, indent=2)
    return f"{kept}\n... (完整列表: {total} 项，已显示前 3 项)"


def _compress_entity_list(items: list) -> list:
    """Compress a list of entity dicts: keep ID + identity fields, truncate long text."""
    compressed = []
    for item in items:
        if isinstance(item, dict):
            c = {}
            for kk, vv in item.items():
                if kk in ("id", "title", "name", "sort_order", "rel_type", "label"):
                    c[kk] = vv
                elif isinstance(vv, str) and len(vv) > 120:
                    c[kk] = vv[:60] + f"... [{len(vv)} chars]"
                elif isinstance(vv, list):
                    # Nested list (e.g. chapters inside acts) — recurse
                    c[kk] = _compress_entity_list(vv) if vv else vv
                else:
                    c[kk] = vv
            compressed.append(c)
        else:
            compressed.append(item)
    return compressed


def _summarise_json_dict(parsed: dict, max_chars: int, raw_data: str, tool_name: str = "") -> str:
    """Summarise a JSON dict: truncate long strings and long arrays."""
    truncated = {}
    for k, v in parsed.items():
        if isinstance(v, str) and len(v) > 500:
            truncated[k] = v[:200] + f"... [{len(v)} chars]"
        elif isinstance(v, list) and len(v) > 5:
            if tool_name in _LIST_TOOL_NAMES:
                # Keep all items for ID lookup; truncate per-item fields
                compressed = _compress_entity_list(v)
                truncated[k] = compressed
            else:
                truncated[k] = v[:3] + [f"... ({len(v)} 项)"]
        else:
            truncated[k] = v
    result = json.dumps(truncated, ensure_ascii=False, indent=2)
    if len(result) > max_chars:
        result = result[:max_chars] + f"\n... [truncated, {len(raw_data)} chars total]"
    return result


def _summarise_tool_output(tool_name: str, result: ToolResult, tool: BaseTool | None = None) -> dict[str, Any]:
    """Truncate very long tool outputs to avoid blowing up the context.

    Uses the tool's ``max_result_chars`` when available, otherwise falls
    back to a heuristic based on tool category.

    Always returns ``data`` as a **string** (never a raw dict/list), so
    callers in ``loop.py`` can rely on it being plain text.  Dict/list
    data is JSON-serialized first, then structure-aware truncation is
    applied to preserve entity IDs.
    """
    # Use the tool's own configured limit as the ceiling, but tighten it for
    # analysis/structural categories whose outputs are typically long and
    # mostly consumed for ID lookup (this branch used to be dead because
    # ``_effective_max_result_chars`` always exists via the BaseTool property).
    max_chars = tool._effective_max_result_chars if tool is not None else 8000
    if tool_name in _ANALYSIS_TOOL_NAMES:
        max_chars = min(max_chars, _ANALYSIS_TOOL_QUOTAS.get(tool_name, 2000))
    elif tool_name in _STRUCTURAL_TOOL_NAMES:
        max_chars = min(max_chars, 4000)

    data = result.data
    # Normalize dict/list to JSON string first so _smart_summarise
    # can apply structure-aware truncation (preserving first items + IDs).
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False, default=str)
    if isinstance(data, str) and len(data) > max_chars:
        data = _smart_summarise(data, max_chars, tool_name)
    elif data is None or data == "":
        data = "(empty)"
    out = {
        "tool": tool_name,
        "success": result.success,
        "data": data,
        "error": result.error,
    }
    # Forward correction_hint to the LLM tool result message
    if result.correction_hint:
        out["correction_hint"] = result.correction_hint
    return out


# ---------------------------------------------------------------------------
# StreamingToolExecutor
# ---------------------------------------------------------------------------

class StreamingToolExecutor:
    """Executes tools as they arrive from a streaming LLM response.

    Lifecycle::

        executor = StreamingToolExecutor(tools, db)

        # Phase 1 – during streaming
        for chunk in llm_client.chat_stream_with_tools(...):
            if chunk.tool_call:
                executor.add_tool(chunk.tool_call, tool_use_id=...)
            for result in executor.get_completed_results():
                yield {"type": "tool_done", "data": result}

        # Phase 2 – after stream ends
        for result in executor.get_completed_results():
            yield {"type": "tool_done", "data": result}

        # If the model fallback path is taken, discard pending work:
        executor.discard()

    Concurrency rules:
      * SAFE tools execute immediately, serialised through an :class:`asyncio.Lock`
        because all tools share a single :class:`AsyncSession` instance which
        is **not** safe for concurrent use.
      * EXCLUSIVE tools are queued and execute serially *after* all SAFE
        tools complete.
    """

    def __init__(self, tools: dict[str, BaseTool], db: Any = None,
                 project_id: str = "", user_id: str = "") -> None:
        self._tools = tools
        self._db = db
        self._project_id = project_id
        self._user_id = user_id

        # Serialise SAFE tool access — AsyncSession is not coroutine-safe
        self._safe_lock = asyncio.Lock()

        # Pending async tasks (tool_use_id -> (tool_name, Task))
        self._pending: dict[str, tuple[str, asyncio.Task]] = {}

        # Completed results: list of (tool_use_id, result_dict)
        self._completed: list[tuple[str, dict]] = []

        # Queued EXCLUSIVE tools: (tool_name, args, tool_use_id)
        self._queued_exclusive: list[tuple[str, dict, str]] = []

        self._discarded = False

        # Monotonic id generator for tool_use blocks that arrive without an id
        # (prevents key collisions in _pending and _completed when the model
        # omits tool_use ids).
        self._id_seq = 0

        # How many items from _completed have been yielded via get_completed_results.
        # Used by await_pending_safe to avoid re-yielding duplicate tool_done events.
        self._completed_yielded: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_tool(
        self,
        tool_use: Any,
        tool_use_id: str = "",
    ) -> None:
        """Called when a complete tool_use block arrives in the stream.

        Args:
            tool_use: A ``ToolCall`` instance or dict with ``function.name``
                      and ``function.arguments``.
            tool_use_id: The tool use block id from the LLM.

        Mode-gating is NOT performed here — that's the interceptor layer's
        job.  The executor simply queues tools by concurrency mode.
        """
        if self._discarded:
            return

        # Extract tool name and args from various input shapes
        tool_name, args = _extract_tool_call_info(tool_use)
        if not tool_name:
            logger.warning("StreamingToolExecutor.add_tool: could not extract tool name")
            return

        tool_use_id = self._coerce_tool_use_id(tool_name, tool_use_id)

        # Resolve tool instance
        tool = self._tools.get(tool_name)
        if not tool:
            err = {
                "tool": tool_name,
                "success": False,
                "error": f"Tool '{tool_name}' not found",
                "_tool_use_id": tool_use_id,
            }
            self._completed.append((tool_use_id, err))
            return

        # Determine concurrency
        concurrency = tool._effective_concurrency

        if concurrency == ConcurrencyMode.SAFE:
            task = asyncio.create_task(
                self._execute_tool(tool_name, args, tool_use_id)
            )
            self._pending[tool_use_id or tool_name] = (tool_name, task)
        elif concurrency == ConcurrencyMode.EXCLUSIVE:
            self._queued_exclusive.append((tool_name, args, tool_use_id))
        else:
            # Unknown concurrency mode – treat as EXCLUSIVE
            self._queued_exclusive.append((tool_name, args, tool_use_id))

    def get_completed_results(self) -> list[dict]:
        """Yield completed results during streaming.

        Non-blocking — only returns tasks that have already finished.
        Call this for every chunk or at a polling interval during the stream.

        Returns:
            List of result dicts for tools that have completed since the
            last call (including ``add_tool``-stage errors such as
            "tool not found").  Each dict has ``{tool, success, data, error,
            _tool_use_id}``.
        """
        done_ids: list[str] = []
        for tid, (tool_name, task) in list(self._pending.items()):
            if task.done():
                done_ids.append(tid)
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    result = {"tool": tool_name, "success": False, "error": "Cancelled", "_tool_use_id": tid}
                except Exception as exc:
                    result = {"tool": tool_name, "success": False, "error": str(exc), "_tool_use_id": tid}
                self._completed.append((result.get("_tool_use_id", tid), result))

        for tid in done_ids:
            del self._pending[tid]

        # Return everything not yet handed to the frontend — this also picks
        # up add_tool-stage errors appended directly to _completed.
        new_slice = self._completed[self._completed_yielded:]
        self._completed_yielded = len(self._completed)
        return [r for _, r in new_slice]

    # ── Split API for interceptor-aware execution ──────────────────────
    # The autonomous loop calls these in order:
    #   1. add_tool() during streaming (SAFE runs immediately; EXCL queued)
    #   2. await_pending_safe() after stream — waits for in-flight SAFE tools
    #   3. get_queued_tools() — returns queued EXCL tools (no execution)
    #   4. interceptor decides which tools are allowed
    #   5. execute_tool() — public, called by loop for each allowed tool
    #   6. clear_queued() — discard tools blocked by interceptor

    async def await_pending_safe(self) -> list[dict]:
        """Wait for in-flight SAFE tools only. Does NOT touch queued tools.

        Returns:
            **All** results accumulated this round — including those already
            yielded to the frontend via ``get_completed_results`` and
            ``add_tool``-stage errors.  The caller needs the complete set to
            build one ``role=tool`` message per tool_call_id (OpenAI/DeepSeek
            reject an assistant tool_call without a matching tool response).
            Frontend-event dedup is the caller's responsibility (track which
            ``_tool_use_id`` values were already emitted during streaming).
        """
        if self._discarded:
            return []

        if self._pending:
            tasks = [task for _, task in self._pending.values()]
            await asyncio.gather(*tasks, return_exceptions=True)
            for tid, (tool_name, task) in list(self._pending.items()):
                try:
                    result = task.result()
                except asyncio.CancelledError:
                    result = {"tool": tool_name, "success": False, "error": "Cancelled", "_tool_use_id": tid}
                except Exception as exc:
                    result = {"tool": tool_name, "success": False, "error": str(exc), "_tool_use_id": tid}
                self._completed.append((result.get("_tool_use_id", tid), result))
            self._pending.clear()

        self._completed_yielded = len(self._completed)
        return [r for _, r in self._completed]

    def get_queued_tools(self) -> list[tuple[str, dict, str]]:
        """Return queued EXCLUSIVE tools without executing them.

        Returns:
            List of ``(tool_name, args, tool_use_id)`` tuples.
        """
        return list(self._queued_exclusive)

    def clear_queued(self) -> None:
        """Discard queued EXCLUSIVE tools without executing them.

        Called when the interceptor blocks, needs confirmation, or captures
        options — the queued tools should not execute.
        """
        self._queued_exclusive.clear()

    def discard(self) -> None:
        """Discard all pending and queued work.

        Called when the model fallback path is taken (e.g. the streaming LLM
        call failed and a different model is being tried), or when the user
        cancels mid-stream.
        """
        self._discarded = True
        for _, task in self._pending.values():
            task.cancel()
        self._pending.clear()
        self._queued_exclusive.clear()
        self._completed.clear()
        self._completed_yielded = 0

    # ------------------------------------------------------------------
    # Public execution entry-point (for interceptor-aware callers)
    # ------------------------------------------------------------------

    def _coerce_tool_use_id(self, name: str, tool_use_id: str) -> str:
        """Return a stable, unique tool_use_id, synthesising one when absent.

        Models occasionally emit tool_use blocks without an ``id``.  Colliding
        empty ids would silently overwrite entries in ``_pending`` /
        ``_completed`` and break frontend dedup by ``_tool_use_id``.
        """
        if tool_use_id:
            return tool_use_id
        self._id_seq += 1
        return f"call_{name}_{self._id_seq}"

    async def execute_tool(
        self, name: str, args: dict, tool_use_id: str = ""
    ) -> dict:
        """Execute a single tool and return a result dict.

        Public — called by the autonomous loop for tools that passed the
        interceptor gates.  Also called internally by ``_execute_tool``.
        """
        return await self._execute_tool(name, args, tool_use_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_tool(
        self, name: str, args: dict, tool_use_id: str = ""
    ) -> dict:
        """Execute a single tool and return a result dict.

        The result always includes ``_tool_use_id`` so callers can correlate
        results with their originating tool-use blocks.
        """
        tool = self._tools.get(name)
        if not tool:
            return {"tool": name, "success": False, "error": "Tool not found", "_tool_use_id": tool_use_id}

        tool_use_id = self._coerce_tool_use_id(name, tool_use_id)

        # Inject project_id and user_id into tool args if missing (LLM often
        # omits them on chained tool calls).  The tool descriptions in the
        # prompt also note that project_id is auto-injected.
        merged = dict(args)
        if self._project_id and "project_id" not in merged:
            merged["project_id"] = self._project_id
        if self._user_id and "user_id" not in merged:
            merged["user_id"] = self._user_id

        timeout = tool._effective_timeout if tool._effective_timeout else 30
        uses_own_session = bool(getattr(tool, "_uses_own_session", False))

        try:
            # SAFE tools that DON'T manage their own session share the
            # executor's AsyncSession (not coroutine-safe), so serialise their
            # DB access through the executor lock. Own-session tools (e.g. the
            # long-running check_consistency) never touch the shared session,
            # so they run lock-free — otherwise a minutes-long check would
            # block every other SAFE tool in the loop.
            if tool._effective_concurrency == ConcurrencyMode.SAFE and not uses_own_session:
                async with self._safe_lock:
                    try:
                        result: ToolResult = await asyncio.wait_for(
                            tool.run(db=self._db, **merged),
                            timeout=timeout,
                        )
                    except asyncio.TimeoutError:
                        # Roll back INSIDE the lock: a concurrent SAFE tool may
                        # be mid-transaction, and rolling back here (while it
                        # holds the session) would abort its work too.
                        try:
                            await self._db.rollback()
                        except Exception:
                            pass
                        return {"tool": name, "success": False, "error": f"工具执行超时（{timeout}秒），事务已回滚", "_tool_use_id": tool_use_id}
                    except KeyError:
                        # Missing-param KeyError is handled by the outer handler
                        # so the result carries a correction_hint.
                        raise
                    except Exception as exc:
                        # The failed tool.run() poisoned the shared session —
                        # roll back INSIDE the lock so a concurrent SAFE tool
                        # holding the same session is not aborted.
                        logger.exception("Tool '%s' execution failed (shared session)", name)
                        try:
                            await self._db.rollback()
                        except Exception:
                            pass
                        return {"tool": name, "success": False, "error": str(exc), "_tool_use_id": tool_use_id}
            else:
                try:
                    result: ToolResult = await asyncio.wait_for(
                        tool.run(db=self._db, **merged),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    # Only roll back the shared session if this tool actually
                    # used it; an own-session tool timed out while a *different*
                    # SAFE tool may hold the shared session mid-transaction.
                    if not uses_own_session:
                        try:
                            await self._db.rollback()
                        except Exception:
                            pass
                    return {"tool": name, "success": False, "error": f"工具执行超时（{timeout}秒）", "_tool_use_id": tool_use_id}
            d = _summarise_tool_output(name, result, tool)
            d["_tool_use_id"] = tool_use_id
            # Forward the (merged) invocation params so the ID registry can tell
            # a filtered list_* result from an authoritative full snapshot, and
            # so project_updated events can report actual changes.
            d["params"] = merged
            return d
        except asyncio.CancelledError:
            raise
        except KeyError as ke:
            # "参数缺失" matches loop.py's cascade-fuse detection so a model
            # that keeps omitting params gets the stop-and-list_* reminder.
            return {"tool": name, "success": False,
                    "error": f"参数缺失: 工具需要 {ke} 参数但未提供。请检查工具描述中的 required 参数并全部提供",
                    "correction_hint": f"下一步：先使用读取工具获取所需参数（缺失: {ke}），拿到后再重新调用本工具",
                    "_tool_use_id": tool_use_id}
        except Exception as exc:
            logger.exception("Tool '%s' execution failed", name)
            # A failed tool.run() poisons the shared session.  SAFE+shared
            # tools roll back inside the lock above; the remaining paths
            # (EXCLUSIVE tools sharing the session) roll back here, while
            # own-session tools never touch the shared session.
            if not uses_own_session:
                try:
                    await self._db.rollback()
                except Exception:
                    pass
            return {"tool": name, "success": False, "error": str(exc), "_tool_use_id": tool_use_id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tool_call_info(tool_use: Any) -> tuple[str, dict]:
    """Extract (tool_name, args) from various input shapes.

    Accepts:
      - ``ToolCall`` dataclass (has ``.function`` dict with name/arguments)
      - ``StreamChunk`` dataclass (has ``.tool_call`` which is a ToolCall)
      - Plain dict with ``function.name`` and ``function.arguments``
    """
    # StreamChunk
    if hasattr(tool_use, "tool_call") and tool_use.tool_call is not None:
        tool_use = tool_use.tool_call

    # ToolCall
    if hasattr(tool_use, "function") and tool_use.function:
        fn = tool_use.function
        name = fn.get("name", "") if isinstance(fn, dict) else getattr(fn, "name", "")
        arguments = fn.get("arguments", "{}") if isinstance(fn, dict) else getattr(fn, "arguments", "{}")
    # Dict
    elif isinstance(tool_use, dict):
        fn = tool_use.get("function", {})
        name = fn.get("name", "")
        arguments = fn.get("arguments", "{}")
    else:
        return "", {}

    if isinstance(arguments, str):
        try:
            args = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            args = {}
    elif isinstance(arguments, dict):
        args = arguments
    else:
        args = {}

    name = name.strip()
    return name, args
