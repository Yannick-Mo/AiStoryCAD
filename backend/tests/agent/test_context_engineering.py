"""Tests for the context-engineering review round:

1. Pair-aware compression tail (tool messages keep their assistant).
2. Token-based compression trigger / anti-oscillation watermark.
3. Structural framework truncation (complete prefix, accurate note).
4. Middle-LLM tool-result compression (whitelist, thresholds, validation).
5. Dead-code removal (check_turn_continuation, aggregate folding).
"""

import inspect

from app.agent.context_compressor import (
    _pair_safe_tail_start,
    compress_history,
    should_compress,
)
from app.agent.loop import (
    _FRAMEWORK_SECTION_MAX,
    _render_framework_section,
)
from app.agent.loop_state import LoopState
from app.agent.middle_compress import (
    COMPRESSIBLE_TOOLS,
    LLM_COMPRESS_MIN_CHARS,
    _fold_fallback,
    _validate,
    should_middle_process,
)
from app.llm.types import Message, ToolCall
from uuid import uuid4


def _tool_pair() -> list[Message]:
    """assistant(tool_calls) + tool reply pair."""
    assistant = Message(
        role="assistant",
        content="调用工具",
        tool_calls=[ToolCall(id="tc_1", function={"name": "read_scene", "arguments": "{}"})],
    )
    tool = Message(role="tool", content="[操作成功]\n工具结果", tool_call_id="tc_1")
    return [assistant, tool]


class TestPairSafeTail:
    def test_naive_tail_lands_on_tool_pulls_assistant_in(self):
        # Tail starts on the tool message; its assistant is in the middle.
        msgs = [
            Message(role="user", content="u0"),
            *_tool_pair(),          # indices 1 (assistant), 2 (tool)
            Message(role="user", content="u3"),
        ]
        s = _pair_safe_tail_start(msgs, head_count=1, tail_count=2)
        assert s == 1  # pulled back to the assistant index
        assert msgs[s].role == "assistant"
        assert msgs[s].tool_calls

    def test_tail_tool_with_assistant_in_head_stays_put(self):
        msgs = [Message(role="user", content="u0"), *_tool_pair()]
        # head_count large enough to include the assistant → no shift needed
        s = _pair_safe_tail_start(msgs, head_count=2, tail_count=1)
        assert s == 2  # unchanged
        assert msgs[s].role == "tool"  # assistant kept in head, safe

    def test_no_tool_at_tail_is_unchanged(self):
        msgs = [Message(role="user", content="a"), Message(role="assistant", content="b")]
        s = _pair_safe_tail_start(msgs, head_count=1, tail_count=1)
        assert s == len(msgs) - 1

    def test_compress_history_preserves_tool_pair(self):
        filler = [Message(role="user", content="内容" * 400) for _ in range(20)]
        msgs = filler + _tool_pair()
        out = compress_history(msgs, model_limit=100, head_count=5, tail_count=2)
        tool_idx = next(i for i, m in enumerate(out) if m.role == "tool")
        prev_assistant = next(
            (m for m in reversed(out[:tool_idx]) if m.role == "assistant"), None
        )
        assert prev_assistant is not None and prev_assistant.tool_calls


class TestTokenBasedCompression:
    def test_should_compress_trips_on_tokens_not_count(self):
        # ~55k CJK chars ≈ 82.5k tokens > 80% of 100k → trips
        big = [Message(role="user", content="字" * 55_000)]
        assert should_compress(big, model_limit=100_000)
        small = [Message(role="user", content="hi")]
        assert not should_compress(small, model_limit=100_000)

    def test_loop_uses_token_trigger_not_count(self):
        import app.agent.loop as loop_mod

        src = inspect.getsource(loop_mod)
        assert "should_compress(state.messages, MODEL_CONTEXT_LIMIT)" in src
        assert "_last_compress_tokens" in src
        assert "_last_scan_count" not in src


class TestFrameworkStructuralTruncation:
    def _big_context(self) -> dict:
        return {
            "acts": [
                {
                    "id": f"a{i}",
                    "name": f"第{i}幕",
                    "sort_order": i,
                    "chapters": [
                        {
                            "id": f"c{i}_{j}",
                            "title": f"第{j}章",
                            "sort_order": j,
                            "scenes": [
                                {"id": f"s{i}_{j}_{k}", "title": f"场景{k}", "sort_order": k}
                                for k in range(10)
                            ],
                        }
                        for j in range(60)
                    ],
                }
                for i in range(4)
            ],
            "characters": [{"id": f"ch{i}", "name": f"角色{i}", "role": "protagonist"} for i in range(50)],
            "relations": [{"character_name": "a", "target_name": "b", "label": "敌对"} for _ in range(30)],
            "edges": [{"source_title": "s", "target_title": "t", "edge_type": "causal"} for _ in range(30)],
        }

    def test_truncation_stops_at_complete_line(self):
        state = LoopState.from_initial({"project_context": self._big_context()})
        out = _render_framework_section(state)
        # Never exceeds budget by more than the truncation note
        assert len(out) <= _FRAMEWORK_SECTION_MAX + 200
        # Ends with the structural note, not a half-rendered line
        assert out.rstrip().endswith("read_chapters 按全局章号范围读取]")
        assert "项目框架较长" in out
        assert "以下未完整列出" in out

    def test_act_index_lists_all_acts_even_when_details_truncated(self):
        # Detail tree only fits the first acts, but the act index must still
        # name EVERY act with its chapter/scene counts (so the model can
        # compute global chapter numbers and jump with read_chapters).
        state = LoopState.from_initial({"project_context": self._big_context()})
        out = _render_framework_section(state)
        assert "幕" in out
        for i in range(4):
            act_name = f"第{i}幕"
            assert act_name in out, f"act index lost act: {act_name}"
            assert f"(act_id=a{i}): 60章/600场" in out, f"counts missing for {act_name}"

    def test_act_index_counts_scenes_per_chapter(self):
        ctx = {
            "acts": [
                {
                    "id": "a1", "name": "第一幕", "sort_order": 1,
                    "chapters": [
                        {"id": "c1", "title": "第一章", "sort_order": 1,
                         "scenes": [{"id": "s1", "title": "开场", "sort_order": 1},
                                    {"id": "s2", "title": "冲突", "sort_order": 2}]},
                        {"id": "c2", "title": "第二章", "sort_order": 2,
                         "scenes": [{"id": "s3", "title": "高潮", "sort_order": 1}]},
                    ],
                }
            ],
        }
        state = LoopState.from_initial({"project_context": ctx})
        out = _render_framework_section(state)
        assert "(act_id=a1): 2章/3场" in out

    def test_small_context_untruncated(self):
        ctx = {
            "acts": [
                {
                    "id": "a1", "name": "第一幕", "sort_order": 1,
                    "chapters": [{"id": "c1", "title": "第一章", "sort_order": 1,
                                  "scenes": [{"id": "s1", "title": "开场", "sort_order": 1}]}],
                }
            ],
            "characters": [{"id": "ch1", "name": "主角", "role": "protagonist"}],
        }
        state = LoopState.from_initial({"project_context": ctx})
        out = _render_framework_section(state)
        assert "项目框架较长" not in out
        assert "第一幕" in out and "主角" in out
        assert "(act_id=a1): 1章/1场" in out


class TestMiddleCompressWhitelist:
    def test_whitelisted_small_result_verbatim_no_llm(self):
        # web_fetch with a typical 2K-char page → below threshold, passthrough
        result = {"tool": "web_fetch", "success": True, "data": {"url": "https://x.com", "content": "正文" * 600}}
        assert not should_middle_process("web_fetch", result)

    def test_whitelisted_large_result_triggers(self):
        result = {"tool": "web_fetch", "success": True, "data": {"url": "https://x.com", "content": "正文" * (LLM_COMPRESS_MIN_CHARS + 100)}}
        assert should_middle_process("web_fetch", result)

    def test_project_query_tools_never_compress(self):
        # DB project-query results (outline/framework + scene/character data)
        # are always injected verbatim — the loop LLM needs exact structure.
        for tool in ("read_scene", "read_chapters", "read_chapter"):
            result = {"tool": tool, "success": True, "data": "数据" * (LLM_COMPRESS_MIN_CHARS + 100)}
            assert not should_middle_process(tool, result), tool

    def test_id_list_tools_never_compress(self):
        # list_* results are navigation maps for tool chaining — never compressed
        big_list = {"tool": "list_relations", "success": True, "data": [{"id": f"id{i}", "title": f"关系{i}"} for i in range(500)]}
        assert not should_middle_process("list_relations", big_list)

    def test_search_cleaned_not_compressed(self):
        small = {"tool": "web_search", "success": True, "data": [{"title": "t", "url": "https://x.com", "snippet": "s"}]}
        assert not should_middle_process("web_search", small)

    def test_failed_result_never_processed(self):
        err = {"tool": "web_fetch", "success": False, "error": "boom"}
        assert not should_middle_process("web_fetch", err)


class TestMiddleCompressValidation:
    def test_id_loss_rejected(self):
        id1, id2 = str(uuid4()), str(uuid4())
        original = {"url": "https://a.com/x", "content": f"关键事实 {id1} 与 {id2} 的关联"}
        out = "关键事实"  # dropped all UUIDs
        assert _validate("web_fetch", original, out) is not None

    def test_no_compression_rejected(self):
        data = "字" * 30_000
        out = "字" * 24_000  # 80% of input → not compressed enough
        assert _validate("web_fetch", data, out) is not None

    def test_compressed_output_with_all_ids_accepted(self):
        id1, id2 = str(uuid4()), str(uuid4())
        original = {"url": "https://x.com/y", "content": f"甲：{id1} 乙：{id2} " + "长" * 500}
        out = f"甲：{id1} 乙：{id2} 摘要"
        assert _validate("web_fetch", original, out) is None

    def test_web_search_url_loss_rejected(self):
        original = [{"title": "a", "url": "https://a.com/x"}, {"title": "b", "url": "https://b.com/y"}]
        out = "a: https://a.com/x"  # b's URL lost
        assert _validate("web_search", original, out) is not None

    def test_web_search_clean_does_not_need_shrink(self):
        # Cleaning is not compression — output may stay similar in size
        original = "标题：a https://a.com/x\n内容：" + "噪声" * 1000
        out = "标题：a https://a.com/x\n内容：核心事实" + "字" * 900
        assert _validate("web_search", original, out) is None

    def test_fold_fallback_keeps_marker(self):
        out = _fold_fallback({"tool": "web_fetch", "success": True, "data": "字" * 60_000})
        assert "压缩失败" in out or "仅保留" in out


class TestMiddleCompressEndToEnd:
    async def test_success_path_returns_llm_output(self):
        from app.agent.middle_compress import middle_process_tool_result

        async def fake_chat(messages, **kwargs):
            assert len(messages) == 2  # system + user, no history
            assert messages[0].role == "system"
            return type("R", (), {"content": "清洗后的网页内容"})

        result = {"tool": "web_fetch", "success": True, "data": {"url": "https://x.com", "content": "正文" * (LLM_COMPRESS_MIN_CHARS + 100)}}
        out = await middle_process_tool_result("web_fetch", result, fake_chat)
        assert out == "清洗后的网页内容"

    async def test_llm_failure_falls_back_to_verbatim(self):
        from app.agent.middle_compress import middle_process_tool_result

        async def broken_chat(messages, **kwargs):
            raise RuntimeError("middle LLM down")

        data = "字" * 20_000
        result = {"tool": "web_fetch", "success": True, "data": {"url": "https://x.com", "content": data}}
        out = await middle_process_tool_result("web_fetch", result, broken_chat)
        assert out.startswith("[操作成功]")
        assert data in out  # verbatim, not folded (under FOLD_FALLBACK_CHARS)

    async def test_huge_llm_failure_falls_back_to_fold(self):
        from app.agent.middle_compress import middle_process_tool_result

        async def broken_chat(messages, **kwargs):
            raise RuntimeError("middle LLM down")

        data = "字" * 60_000
        result = {"tool": "web_fetch", "success": True, "data": {"url": "https://x.com", "content": data}}
        out = await middle_process_tool_result("web_fetch", result, broken_chat)
        assert "仅保留" in out or "压缩失败" in out
        assert data not in out

    async def test_invalid_output_rejected_and_fallback(self):
        from app.agent.middle_compress import middle_process_tool_result

        async def hallucinating_chat(messages, **kwargs):
            return type("R", (), {"content": "简短回复"})  # dropped all IDs

        id1, id2 = str(uuid4()), str(uuid4())
        data = {"url": "https://a.com", "content": f"甲：{id1} 乙：{id2} " + "长" * 100}
        result = {"tool": "web_fetch", "success": True, "data": data}
        out = await middle_process_tool_result("web_fetch", result, hallucinating_chat)
        assert "简短回复" not in out  # rejected
        assert "长" in out  # original injected

    async def test_non_whitelisted_large_result_verbatim_no_llm_called(self):
        from app.agent.middle_compress import middle_process_tool_result

        called = {"flag": False}

        async def fake_chat(messages, **kwargs):
            called["flag"] = True
            return type("R", (), {"content": "nope"})

        result = {"tool": "update_chapter", "success": True, "data": "x" * 30_000}
        out = await middle_process_tool_result("update_chapter", result, fake_chat)
        assert not called["flag"]
        assert out.startswith("[操作成功]")

    async def test_project_query_tools_verbatim_no_llm_called(self):
        from app.agent.middle_compress import middle_process_tool_result

        called = {"flag": False}

        async def fake_chat(messages, **kwargs):
            called["flag"] = True
            return type("R", (), {"content": "nope"})

        for tool in ("read_scene", "read_chapters", "read_chapter"):
            result = {"tool": tool, "success": True, "data": "数据" * (LLM_COMPRESS_MIN_CHARS + 100)}
            out = await middle_process_tool_result(tool, result, fake_chat)
            assert not called["flag"], tool
            assert out.startswith("[操作成功]")
            assert "数据" in out  # full original data injected verbatim


class TestRecoveryFatalAccountErrors:
    def test_insufficient_balance_gives_up_immediately(self):
        from app.agent.recovery import ErrorClassifier, RecoveryAction
        decision = ErrorClassifier.classify(
            'LLM API error 402: {"error":{"message":"Insufficient Balance"}}', attempt=0,
        )
        assert decision.action == RecoveryAction.GIVE_UP
        assert "余额" in decision.message

    def test_401_unauthorized_gives_up_immediately(self):
        from app.agent.recovery import ErrorClassifier, RecoveryAction
        decision = ErrorClassifier.classify("LLM API error 401: Unauthorized", attempt=0)
        assert decision.action == RecoveryAction.GIVE_UP

    def test_transient_still_retries(self):
        from app.agent.recovery import ErrorClassifier, RecoveryAction
        decision = ErrorClassifier.classify("LLM API error 503: overloaded", attempt=0)
        assert decision.action == RecoveryAction.RETRY


class TestDeadCodeRemoved:
    def test_check_turn_continuation_gone(self):
        import app.agent.token_budget as tb
        assert not hasattr(tb, "check_turn_continuation")
        assert not hasattr(tb, "CONTINUATION_LIMIT")

    def test_aggregate_folding_gone(self):
        import app.agent.token_budget as tb
        assert not hasattr(tb, "MAX_TOOL_RESULT_TOKENS")
        assert not hasattr(tb, "build_tool_message_content")

    def test_loop_does_not_reference_it(self):
        import app.agent.loop as loop_mod
        assert not hasattr(loop_mod, "check_turn_continuation")
        assert not hasattr(loop_mod, "_tool_budget_exceeded")
        src = inspect.getsource(loop_mod)
        assert "check_turn_continuation" not in src


class TestStateFieldRename:
    def test_last_compress_tokens_in_state(self):
        st = LoopState.from_initial({})
        assert hasattr(st, "_last_compress_tokens")
        assert not hasattr(st, "_last_scan_count")
        d = st.to_dict()
        assert "_last_compress_tokens" in d
        assert "_last_scan_count" not in d
