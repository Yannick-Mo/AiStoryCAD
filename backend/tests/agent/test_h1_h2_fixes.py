"""Regression tests for H1 (redundant tool_summary injection), H2
(project framework loaded but never injected into the LLM context),
M11 (confirmation placeholders) and M22 (boundary messages + tool_done
correlation)."""

import inspect
from types import SimpleNamespace

from app.agent.attachments import AttachmentInjector
from app.agent.context_compressor import build_boundary_message
from app.agent.loop import _render_framework_section
from app.agent.loop_state import LoopState


class TestH1NoToolSummaryInjection:
    """H1: tool results were injected 4 ways (role=tool + tool_summary +
    id_registry + tool_results).  tool_summary was removed — the full results
    already live in role=tool messages and id_registry gives the ID index."""

    def test_build_system_sections_has_no_tool_summary(self):
        injector = AttachmentInjector()
        state = SimpleNamespace(
            tool_results=[
                {"tool": "list_chapters", "success": True, "data": "chapter data"},
            ],
            id_registry={},
            cowriter_session=None,
            pending_plan=None,
            plan_confirmed=False,
            errors=[],
        )
        sections = injector.build_system_sections(state)
        assert "tool_summary" not in sections, (
            "tool_summary still injected — duplicates role=tool messages"
        )

    def test_loop_dynamic_section_names_exclude_tool_summary(self):
        import app.agent.loop as loop_mod

        src = inspect.getsource(loop_mod._build_turn_sections)
        # Find the tuple that lists injected dynamic sections and assert the
        # exact tuple no longer contains tool_summary.
        assert '("id_registry", "session_progress", "plan_reminder", "error_context")' in src
        tuple_line = next(
            line for line in src.splitlines() if "id_registry" in line and "(" in line
        )
        assert "tool_summary" not in tuple_line


class TestH2FrameworkActuallyInjected:
    """H2: the system prompt claimed 项目框架已在上下文中提供 but never
    included it.  _render_framework_section now renders the loaded framework
    into a bounded text section."""

    def _sample_context(self) -> dict:
        return {
            "acts": [
                {
                    "id": "a1",
                    "name": "第一幕",
                    "sort_order": 1,
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "第一章",
                            "sort_order": 1,
                            "scenes": [
                                {"id": "s1", "title": "开场", "sort_order": 1},
                                {"id": "s2", "title": "冲突", "sort_order": 2},
                            ],
                        }
                    ],
                }
            ],
            "characters": [{"id": "ch1", "name": "主角", "role": "protagonist"}],
            "themes": [{"name": "救赎"}],
            "relations": [{"character_name": "主角", "target_name": "反派", "label": "敌对"}],
            "edges": [{"source_title": "第一章", "target_title": "第二章", "edge_type": "causal"}],
        }

    def test_framework_renders_structure_with_ids(self):
        state = LoopState.from_initial({"project_context": self._sample_context()})
        out = _render_framework_section(state)
        assert "第一幕" in out and "act_id=a1" in out
        assert "第一章" in out and "chapter_id=c1" in out
        assert "scene_id=s1" in out and "scene_id=s2" in out
        assert "主角" in out and "character_id=ch1" in out
        assert "救赎" in out
        assert "敌对" in out
        assert "causal" in out

    def test_framework_bounded(self):
        from app.agent.loop import _FRAMEWORK_SECTION_MAX

        ctx = self._sample_context()
        # Add many chapters/scenes to force truncation
        ctx["acts"][0]["chapters"] = [
            {
                "id": f"c{i}",
                "title": f"第{i}章",
                "sort_order": i,
                "scenes": [
                    {"id": f"s{i}_{j}", "title": f"场景{j}", "sort_order": j}
                    for j in range(20)
                ],
            }
            for i in range(120)
        ]
        state = LoopState.from_initial({"project_context": ctx})
        out = _render_framework_section(state)
        assert len(out) <= _FRAMEWORK_SECTION_MAX + 80  # allow truncation marker suffix

    def test_empty_context_returns_empty(self):
        state = LoopState.from_initial({"project_context": {}})
        assert _render_framework_section(state) == ""


class TestM11ConfirmationPlaceholders:
    """M11: the confirmation branch produced an assistant message whose
    tool_use blocks had no matching role=tool response, so the next API call
    was rejected.  Each pending tool now gets an honest placeholder."""

    def test_confirmation_branch_inserts_tool_placeholder(self):
        import app.agent.loop as loop_mod

        src = inspect.getsource(loop_mod.autonomous_loop)
        # Every pending_tool must get a role=tool placeholder carrying its
        # tool_call_id BEFORE the plan is stored/break happens.
        assert '[操作等待确认]' in src
        assert 'tool_call_id=tool_use_id' in src
        assert 'role="tool"' in src
        # The placeholder must be inserted inside the needs_confirmation branch
        # (i.e. before the pending_plan assignment) — not after plan break.
        conf_region = src[src.index("if intercept.needs_confirmation:"):]
        assert conf_region.index("操作等待确认") < conf_region.index("pending_plan=plan")


class TestM22CompressionBoundaryAndToolCorrelation:
    """M22: compression never surfaced a user-visible boundary message and
    hard-failed blocking tools produced tool_done events with no
    _tool_use_id."""

    def test_proactive_compress_inserts_boundary(self):
        import app.agent.loop as loop_mod

        src = inspect.getsource(loop_mod.autonomous_loop)
        assert "build_boundary_message(original_count, len(compressed), reactive=reactive)" in src

    def test_reactive_compress_escalates_if_no_reduction(self):
        import app.agent.loop as loop_mod

        src = inspect.getsource(loop_mod.autonomous_loop)
        # Must not blindly retry after reactive_compress; escalate when the
        # context didn't actually shrink.
        assert "tokens_after < tokens_before" in src
        assert "escalating to recovery" in src

    def test_reactively_compressed_state_gets_boundary(self):
        import app.agent.loop as loop_mod

        src = inspect.getsource(loop_mod.autonomous_loop)
        assert "build_boundary_message(len(state.messages), len(compressed), reactive=True)" in src

    def test_tool_done_except_path_keeps_tool_use_id(self):
        import app.agent.loop as loop_mod

        src = inspect.getsource(loop_mod.autonomous_loop)
        # The hard-failure result must carry _tool_use_id so the frontend and
        # id_registry can correlate the tool_done event to its tool_use block.
        assert '"_tool_use_id": tool_use_id' in src

    def test_boundary_message_is_user_visible_system(self):
        m = build_boundary_message(80, 12, reactive=False)
        assert m.role == "system"
        assert "80 条消息" in m.content
        assert "12 条" in m.content
        assert "自动压缩" in m.content
        mr = build_boundary_message(80, 12, reactive=True)
        assert "紧急压缩" in mr.content
