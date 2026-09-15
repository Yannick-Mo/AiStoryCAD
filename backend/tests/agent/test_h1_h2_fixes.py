"""Regression tests for H1 (redundant tool_summary injection), H2
(project framework loaded but never injected into the LLM context),
M11 (confirmation placeholders) and M22 (boundary messages + tool_done
correlation)."""

import inspect
import re
from types import SimpleNamespace


from app.agent.attachments import AttachmentInjector
from app.agent.context_compressor import (
    build_boundary_message,
    estimate_text_tokens,
)
from app.agent.loop import (
    _ACT_INDEX_MAX_TOKENS,
    _ENTITY_MAX_TOKENS,
    _WINDOW_MAX_TOKENS,
    _render_framework_section,
)
from app.agent.loop_state import LoopState
from tests.agent._fixtures import framework_context


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
        assert '("id_registry", "plan_reminder", "error_context")' in src
        tuple_line = next(
            line for line in src.splitlines() if "id_registry" in line and "(" in line
        )
        assert "tool_summary" not in tuple_line
        assert "session_progress" not in tuple_line


class TestH2FrameworkActuallyInjected:
    """H2：系统提示宣称「项目框架已在上下文中提供」，而当时它根本没被渲染进去。

    常驻地图现在的契约（2026-09 大改）：

    * **幕图**：全部幕 + act_id + 章数 + 起止全局章号 + 这一幕要干什么；
    * **侧挂记录**：未收伏笔（**优先**）→ 人物名单 → 关系；
    * **你在哪**：用户当前查看的章（编辑器传进来的焦点）或正文断点；
    * **附近的章**：以「你在哪」为中心的窗口，只给 章号/标题/状态/已写未写——
      **不含"要写什么"**，那是 read_chapters / read_chapter_package 的事；
    * 截断落在结构边界，并如实标注省略了什么、该用哪个工具去取。
    """

    def _ctx(self) -> dict:
        return framework_context(
            [{"id": "a1", "name": "第一幕", "purpose": "第一幕要干什么",
              "chapters": [{"id": "c1", "title": "第一章"},
                           {"id": "c2", "title": "第二章", "written": True}]}],
            focus_order=2, view_focus=True,
            characters=[{"id": "ch1", "name": "主角", "role": "main"}],
            relations=[{"character_name": "主角", "target_name": "反派",
                        "label": "敌对"}],
            setups=[{"id": "st1", "note": "旧硬币", "status": "open",
                     "planted_in_order": 1, "pay_off_in_order": None}],
        )

    def _render(self, ctx: dict) -> str:
        return _render_framework_section(
            LoopState.from_initial({"project_context": ctx})
        )

    def test_framework_renders_structure_with_ids(self):
        out = self._render(self._ctx())
        assert "第一幕" in out
        assert "act_id=a1" in out
        assert "第一幕要干什么" in out
        assert "2章（全书第1-2章）" in out
        assert "第一章" in out
        assert "chapter_id=c1" in out
        assert "## 未回收的伏笔" in out
        assert "旧硬币" in out
        assert "character_id=ch1" in out
        assert "敌对" in out
        assert "edge_type" not in out

    def test_user_position_is_rendered(self):
        """编辑器传来的「用户正在看第几章」必须真的出现在提示词里。"""
        assert "用户当前查看：第 2 章" in self._render(self._ctx())

    def test_chapter_plans_are_not_resident(self):
        """常驻地图**不给**每章的「要写什么」——大书里那是几百章的细节。"""
        ctx = self._ctx()
        ctx["acts"][0]["chapters"][0]["goal"] = "机密计划"
        assert "机密计划" not in self._render(ctx)

    def test_omission_counts_are_never_negative(self):
        """省略标记里的计数不能出现负数。

        旧实现是拿 ``## `` 前缀去数幕的，而侧挂块的头（``## 人物``）也是
        ``## ``，于是把幕数算成了负数。新实现用真正的计数器，这条守住它。
        """
        ctx = framework_context(
            [{"id": f"a{i}", "name": f"第{i}幕",
              "chapters": [{"title": f"第{j}章"} for j in range(20)]}
             for i in range(1, 5)],
            total_chapter_count=3000,
        )
        out = self._render(ctx)
        assert "未列出" in out
        for line in out.splitlines():
            # 前面有数字说明是"范围连字符"（第 1-80 章）；没有才是负号
            assert not re.search(r"(?<!\d)-\s*\d+\s*(幕|章|条|人)", line), line

    def test_empty_context_returns_empty(self):
        state = LoopState.from_initial({"project_context": {}})
        assert _render_framework_section(state) == ""

    def test_omissions_are_declared_not_silent(self):
        """3000 章的书：常驻里只有你附近那一圈，其余在哪必须说清。"""
        ctx = framework_context(
            [{"id": "a1", "name": "第一幕",
              "chapters": [{"title": f"第{n}章"} for n in range(1932, 2069)]}],
            total_chapter_count=3000, first_order=1932,
            window_span=(1932, 2068), focus_order=2000, view_focus=True,
        )
        out = self._render(ctx)
        assert "第 1932-2068 章" in out
        assert "全书 3000 章" in out, "必须说清这本书一共多长"
        assert "read_chapters" in out
        assert "detail 传 index" in out, "要告诉模型怎么粗扫目录"


class TestResidentMapBudgets:
    """预算按 **token** 算、不按字符，且三类内容各有各的上限。"""

    def _render(self, ctx: dict) -> str:
        return _render_framework_section(
            LoopState.from_initial({"project_context": ctx})
        )

    def test_window_is_bounded_and_says_so(self):
        ctx = framework_context(
            [{"id": "a1", "name": "第一幕",
              "chapters": [{"title": f"第{i}章"} for i in range(1, 401)]}],
            total_chapter_count=3000,
        )
        out = self._render(ctx)
        budget = _ACT_INDEX_MAX_TOKENS + _WINDOW_MAX_TOKENS + _ENTITY_MAX_TOKENS
        assert estimate_text_tokens(out) <= budget + 400, \
            f"{estimate_text_tokens(out)} tokens 超过预算 {budget}"
        assert "未列出" in out, "截断了就必须说"

    def test_act_index_lists_every_act_at_realistic_sizes(self):
        """真实规模（几十幕）下全部幕都在；只有极端幕数才会被截。"""
        acts = [{"id": f"a{i}", "name": f"第{i}幕",
                 "chapters": [{"title": f"第{i}章"}]} for i in range(1, 41)]
        out = self._render(framework_context(acts))
        for i in range(1, 41):
            assert f"第{i}幕" in out, f"幕图漏了第{i}幕"

    def test_act_index_marks_omissions_when_absurd(self):
        acts = [{"id": f"a{i}", "name": f"第{i}幕"} for i in range(1, 501)]
        out = self._render(framework_context(acts))
        assert "幕未列出" in out

    def test_setups_get_budget_before_rosters(self):
        """三块共享预算：伏笔优先（旧顺序把伏笔放在最后，等于永远先被挤掉）。"""
        ctx = framework_context(
            [{"id": "a1", "name": "第一幕", "chapters": [{"title": "第一章"}]}],
            characters=[{"id": f"ch{i}", "name": f"角色{i}", "role": "main"}
                        for i in range(300)],
            relations=[{"character_name": "甲", "target_name": "乙", "label": "敌对"}
                       for _ in range(300)],
            setups=[{"id": f"st{i}", "note": f"伏笔{i}", "status": "open",
                     "planted_in_order": 1, "pay_off_in_order": None}
                    for i in range(300)],
        )
        out = self._render(ctx)
        assert "## 未回收的伏笔" in out
        assert out.index("## 未回收的伏笔") < out.index("## 人物")
        assert "伏笔0" in out, "伏笔必须最先拿到预算"

    def test_small_project_has_no_truncation_notice(self):
        ctx = framework_context(
            [{"id": "a1", "name": "第一幕",
              "chapters": [{"id": "c1", "title": "第一章"}]}],
            focus_order=1, characters=[{"id": "ch1", "name": "主角", "role": "main"}],
        )
        out = self._render(ctx)
        assert "未列出" not in out
        assert "第一幕" in out
        assert "主角" in out
        assert "1章（全书第1-1章）" in out


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
