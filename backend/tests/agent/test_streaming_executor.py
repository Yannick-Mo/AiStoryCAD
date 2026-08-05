"""Tests for StreamingToolExecutor result bookkeeping.

Covers three bugs:
1. SAFE tools that complete *during* streaming were consumed by
   ``get_completed_results()`` and then excluded from the later
   ``await_pending_safe()`` return — so loop.py never built the mandatory
   ``role=tool`` message for that tool_call_id (OpenAI/DeepSeek 400).
2. ``add_tool``-stage errors ("Tool not found") were appended to
   ``_completed`` but never surfaced anywhere — the model got no feedback.
3. ``_summarise_json_list`` referenced an undefined variable ``data`` in
   its final truncation branch, and ``_smart_summarise`` sent top-level
   JSON arrays down the blind head/tail truncation path (losing IDs).
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolMeta, ToolResult
from app.agent.tools.streaming_executor import (
    StreamingToolExecutor,
    _smart_summarise,
    _summarise_json_list,
)


class FakeSafeTool(BaseTool):
    """A SAFE (read-only) tool that completes immediately."""

    meta = ToolMeta(
        name="list_chapters",
        description="Fake list_chapters for tests",
        parameters={"type": "object", "properties": {}},
        concurrency=ConcurrencyMode.SAFE,
    )

    def __init__(self, data=None):
        super().__init__()
        self._data = data if data is not None else {"chapters": [{"id": "ch-1", "title": "第一章"}]}

    async def run(self, db, **kwargs):
        return ToolResult(success=True, data=self._data)


def _tool_call_dict(name: str) -> dict:
    return {"function": {"name": name, "arguments": "{}"}}


async def _drain_completed(executor: StreamingToolExecutor, timeout: float = 2.0) -> list[dict]:
    """Poll get_completed_results until at least one result arrives."""
    deadline = asyncio.get_event_loop().time() + timeout
    collected: list[dict] = []
    while not collected and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.01)
        collected.extend(executor.get_completed_results())
    return collected


class TestAwaitPendingSafeReturnsAllResults:
    """Bug 1: results consumed mid-stream must still reach await_pending_safe."""

    async def test_result_yielded_midstream_is_included_in_await_pending_safe(self):
        tool = FakeSafeTool()
        executor = StreamingToolExecutor({"list_chapters": tool}, db=AsyncMock())

        executor.add_tool(_tool_call_dict("list_chapters"), tool_use_id="tc_1")

        # Simulate loop.py consuming the result mid-stream
        midstream = await _drain_completed(executor)
        assert len(midstream) == 1
        assert midstream[0]["_tool_use_id"] == "tc_1"

        # After the stream ends, await_pending_safe must STILL return the
        # result so loop.py can build the role=tool message for tc_1.
        final = await executor.await_pending_safe()
        ids = [r.get("_tool_use_id") for r in final]
        assert "tc_1" in ids, (
            "Result consumed via get_completed_results was dropped from "
            "await_pending_safe — tool_call tc_1 would have no tool message"
        )

    async def test_result_not_consumed_midstream_still_returned(self):
        tool = FakeSafeTool()
        executor = StreamingToolExecutor({"list_chapters": tool}, db=AsyncMock())
        executor.add_tool(_tool_call_dict("list_chapters"), tool_use_id="tc_2")

        # No mid-stream consumption at all
        final = await executor.await_pending_safe()
        ids = [r.get("_tool_use_id") for r in final]
        assert "tc_2" in ids

    async def test_multiple_tools_mixed_consumption(self):
        tool = FakeSafeTool()
        executor = StreamingToolExecutor({"list_chapters": tool}, db=AsyncMock())

        executor.add_tool(_tool_call_dict("list_chapters"), tool_use_id="tc_a")
        # Consume the first tool's result mid-stream
        await _drain_completed(executor)
        # Second tool arrives later and is never consumed mid-stream
        executor.add_tool(_tool_call_dict("list_chapters"), tool_use_id="tc_b")

        final = await executor.await_pending_safe()
        ids = {r.get("_tool_use_id") for r in final}
        assert {"tc_a", "tc_b"} <= ids


class TestAddToolErrorsSurface:
    """Bug 2: 'Tool not found' errors must reach the model."""

    async def test_unknown_tool_error_in_await_pending_safe(self):
        executor = StreamingToolExecutor({}, db=AsyncMock())
        executor.add_tool(_tool_call_dict("no_such_tool"), tool_use_id="tc_x")

        final = await executor.await_pending_safe()
        matching = [r for r in final if r.get("_tool_use_id") == "tc_x"]
        assert matching, "add_tool-stage error was silently dropped"
        assert matching[0]["success"] is False
        assert "no_such_tool" in matching[0]["error"]

    async def test_unknown_tool_error_survives_midstream_consumption(self):
        """Even if get_completed_results ran after add_tool, the error must
        still be present in await_pending_safe's return."""
        executor = StreamingToolExecutor({}, db=AsyncMock())
        executor.add_tool(_tool_call_dict("no_such_tool"), tool_use_id="tc_y")

        midstream = executor.get_completed_results()
        # The error should be visible mid-stream too (was previously invisible)
        assert any(r.get("_tool_use_id") == "tc_y" for r in midstream)

        final = await executor.await_pending_safe()
        assert any(r.get("_tool_use_id") == "tc_y" for r in final)


class TestSummariseJsonList:
    """Bug 3: top-level JSON arrays and the NameError truncation branch."""

    def _make_long_array(self, n=50, field_len=300):
        return [
            {"id": f"id-{i}", "title": f"标题{i}", "summary": "长" * field_len}
            for i in range(n)
        ]

    def test_top_level_array_keeps_all_ids(self):
        items = self._make_long_array()
        raw = json.dumps(items, ensure_ascii=False)
        out = _smart_summarise(raw, max_chars=8000, tool_name="list_scenes")
        for i in range(50):
            assert f"id-{i}" in out, f"id-{i} lost in summarised output"

    def test_top_level_array_extreme_truncation_no_nameerror(self):
        """Force the final truncation branch — must not raise NameError."""
        items = self._make_long_array(n=200, field_len=500)
        out = _summarise_json_list(items, max_chars=500, tool_name="list_scenes")
        assert isinstance(out, str)
        assert "truncated" in out

    def test_non_list_tool_array_shows_first_three(self):
        items = self._make_long_array(n=10, field_len=10)
        raw = json.dumps(items, ensure_ascii=False)
        out = _smart_summarise(raw, max_chars=200, tool_name="some_other_tool")
        assert "10 项" in out

    def test_dict_output_unchanged(self):
        data = json.dumps({"key": "v" * 900}, ensure_ascii=False)
        out = _smart_summarise(data, max_chars=300, tool_name="read_scene")
        assert isinstance(out, str)


class TestLoopToolMessageBookkeeping:
    """T4: every tool_call_id in assistant messages gets a role=tool message,
    and no duplicate tool_done events are emitted for the same tool_use_id."""

    async def test_midstream_safe_tool_gets_tool_message(self):
        from app.agent.loop import autonomous_loop
        from app.llm.types import Message, StreamChunk, ToolCall

        call_count = 0

        def fake_stream_with_tools(**kwargs):
            nonlocal call_count
            call_count += 1

            async def turn_one():
                yield StreamChunk(
                    tool_call=ToolCall(
                        id="tc_loop_1",
                        function={"name": "list_chapters", "arguments": "{}"},
                    )
                )
                # Give the SAFE tool task time to finish DURING the stream,
                # so get_completed_results consumes it before stream end.
                await asyncio.sleep(0.1)
                yield StreamChunk(finish_reason="tool_calls")

            async def turn_two():
                yield StreamChunk(content="共有一章。")
                yield StreamChunk(finish_reason="stop")

            return turn_one() if call_count == 1 else turn_two()

        llm = MagicMock()
        llm.chat_stream_with_tools = fake_stream_with_tools

        tools = {"list_chapters": FakeSafeTool()}
        initial = {
            "project_id": "",  # skip Phase 0 context loading
            "user_id": "u1",
            "mode": "chat",
            "messages": [Message(role="user", content="有几章？")],
        }

        events = []
        with patch(
            "app.agent.response_builder.build_system_prompt",
            new=AsyncMock(return_value="SYS"),
        ):
            async for ev in autonomous_loop(initial, tools, llm, AsyncMock(), ""):
                events.append(ev)

        final = [e for e in events if e.get("_loop_done")][0]["final_state"]
        messages = final["messages"]

        # Every tool_call_id on assistant messages must have a role=tool reply
        tool_call_ids = []
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                tool_call_ids.extend(tc.id for tc in m.tool_calls)
        tool_reply_ids = {m.tool_call_id for m in messages if m.role == "tool"}

        assert tool_call_ids, "expected at least one assistant tool_call"
        for tcid in tool_call_ids:
            assert tcid in tool_reply_ids, (
                f"tool_call {tcid} has no matching role=tool message — "
                f"next LLM call would be rejected (orphan tool_call)"
            )

        # No duplicate frontend tool_done events for the same tool_use_id
        done_ids = [
            e["_tool_done"].get("_tool_use_id")
            for e in events
            if "_tool_done" in e and e["_tool_done"].get("_tool_use_id")
        ]
        assert len(done_ids) == len(set(done_ids)), (
            f"duplicate tool_done events emitted: {done_ids}"
        )


class TestEmptyToolUseIdSynthesized:
    """M9: models that omit tool_use ids must not collide in _pending/_completed."""

    async def test_two_safe_tools_without_ids_get_unique_ids(self):
        tool = FakeSafeTool()
        executor = StreamingToolExecutor({"list_chapters": tool}, db=AsyncMock())
        executor.add_tool(_tool_call_dict("list_chapters"))
        executor.add_tool(_tool_call_dict("list_chapters"))

        final = await executor.await_pending_safe()
        ids = [r.get("_tool_use_id") for r in final]
        assert len(ids) == 2, "both tools must produce results"
        assert ids[0] != ids[1], (
            "empty tool_use_id collided — second result overwrote the first"
        )
        assert all(i.startswith("call_list_chapters_") for i in ids)

    async def test_empty_id_result_surfaces_via_get_completed_results(self):
        tool = FakeSafeTool()
        executor = StreamingToolExecutor({"list_chapters": tool}, db=AsyncMock())
        executor.add_tool(_tool_call_dict("list_chapters"))
        midstream = await _drain_completed(executor)
        assert len(midstream) == 1
        assert midstream[0]["_tool_use_id"].startswith("call_list_chapters_")


class _SlowTool(BaseTool):
    meta = ToolMeta(
        name="slow_tool",
        description="Fake slow tool",
        parameters={"type": "object", "properties": {}},
        concurrency=ConcurrencyMode.SAFE,
        timeout=1,
    )

    async def run(self, db, **kwargs):
        await asyncio.sleep(10)
        return ToolResult(success=True, data="late")


class TestTimeoutRollbackInsideLock:
    """M10: a timed-out SAFE tool must roll back inside the executor lock so a
    concurrent SAFE tool's transaction is not aborted."""

    async def test_safe_tool_timeout_does_not_kill_other_tool(self):
        rollback = AsyncMock()
        db = AsyncMock()
        db.rollback = rollback

        executor = StreamingToolExecutor(
            {"slow_tool": _SlowTool(), "list_chapters": FakeSafeTool()},
            db=db,
        )
        executor.add_tool(_tool_call_dict("slow_tool"), tool_use_id="tc_slow")
        executor.add_tool(_tool_call_dict("list_chapters"), tool_use_id="tc_fast")

        final = await executor.await_pending_safe()
        by_id = {r.get("_tool_use_id"): r for r in final}

        assert "超时" in by_id["tc_slow"]["error"]
        assert by_id["tc_fast"]["success"] is True, (
            "rollback after a sibling tool timeout aborted the other SAFE tool"
        )
        rollback.assert_awaited()


class _KeyErrorTool(BaseTool):
    meta = ToolMeta(
        name="keyerror_tool",
        description="Fake tool that raises KeyError",
        parameters={"type": "object", "properties": {}},
        concurrency=ConcurrencyMode.SAFE,
    )

    async def run(self, db, **kwargs):
        kwargs["missing_param"]
        return ToolResult(success=True, data="never")


class TestKeyErrorCarriesCorrectionHint:
    """H6: KeyError from a tool (missing param) must surface correction_hint so
    loop.py builds a 修正提示 message instead of failing silently."""

    async def test_keyerror_result_has_correction_hint(self):
        executor = StreamingToolExecutor({"keyerror_tool": _KeyErrorTool()}, db=AsyncMock())
        executor.add_tool(_tool_call_dict("keyerror_tool"), tool_use_id="tc_k")

        final = await executor.await_pending_safe()
        r = [x for x in final if x.get("_tool_use_id") == "tc_k"][0]
        assert r["success"] is False
        assert r.get("correction_hint"), "KeyError result must carry correction_hint"
        assert "missing_param" in r["error"]


class TestAnalysisCategoryLimitApplies:
    """H8: the dead ``hasattr`` branch meant analysis/structural tools were never
    capped at their tighter category limits — output now truncates."""

    def test_analysis_tool_long_output_is_truncated(self):
        from types import SimpleNamespace

        from app.agent.tools.streaming_executor import _summarise_tool_output

        tool = SimpleNamespace(_effective_max_result_chars=8000)
        long = "长" * 5000
        out = _summarise_tool_output(
            "project_health", ToolResult(success=True, data=long), tool
        )
        assert len(out["data"]) < 5000, (
            "analysis tool output was not capped at the 2000-char category limit"
        )

    def test_non_categorised_tool_keeps_default_limit(self):
        from types import SimpleNamespace

        from app.agent.tools.streaming_executor import _summarise_tool_output

        tool = SimpleNamespace(_effective_max_result_chars=8000)
        data = "x" * 5000
        out = _summarise_tool_output(
            "read_scene", ToolResult(success=True, data=data), tool
        )
        assert out["data"] == data, (
            "5000 chars fits under the 8000 default — must not be truncated"
        )
