"""Tests for the "list IDs first, then call tool" business flow.

Verifies the complete lifecycle of ID-dependent tool calling:
1. Tool filter: list tools available in both modes; write tools only in cowriter
2. ← markers tell the AI which read tool to call first
3. Error messages for missing params guide AI back to the right list tool
4. Mode gate (interceptor) blocks write tools in chat mode

标记的位置随这次重构迁移了：它不再写在工具的 ``meta.description`` 里，而是写在
**参数描述**里（``{"chapter_id": {"description": "章节ID，来自 read_chapters 或 read_acts"}}``）。
``app.agent.tools.get_tool_descriptions()`` 会把 ``来自 X`` 渲染成
``chapter_id ← read_chapters`` 提示——模型看到的正是这个箭头，所以这里断言的是
「参数描述点名了 ID 来源」＋「渲染结果里真的出现了箭头」，比只查工具描述更强。
"""

import pytest
from app.agent.tools import get_tool_registry, get_filtered_tools, get_tool_descriptions
from app.agent.tools.base import BaseTool
from app.agent.tool_filter import READ_ONLY_TOOLS, COWRITER_TOOLS
from app.agent.interceptors import apply_interceptors


# ── Tool sets ───────────────────────────────────────────────────────────────

LIST_TOOLS = {"list_characters", "list_relations", "list_setups"}

READ_TOOLS = READ_ONLY_TOOLS - LIST_TOOLS

WRITE_TOOLS = COWRITER_TOOLS

ALL_MODE_TOOLS = READ_ONLY_TOOLS | WRITE_TOOLS


# ── Tool Filter Tests ──────────────────────────────────────────────────────

class TestListToolsAvailableInBothModes:
    def test_all_list_tools_in_chat_mode(self):
        registry = get_tool_registry()
        chat_tools = get_filtered_tools(registry, mode="chat")
        for name in LIST_TOOLS:
            assert name in chat_tools, \
                f"List tool '{name}' missing in chat mode"

    def test_all_list_tools_in_cowriter_mode(self):
        registry = get_tool_registry()
        cowriter_tools = get_filtered_tools(registry, mode="cowriter")
        for name in LIST_TOOLS:
            assert name in cowriter_tools, \
                f"List tool '{name}' missing in cowriter mode"

    def test_no_write_tools_in_chat_mode(self):
        registry = get_tool_registry()
        chat_tools = get_filtered_tools(registry, mode="chat")
        chat_names = set(chat_tools.keys())
        leaked = chat_names & WRITE_TOOLS
        assert not leaked, f"Write tools leaked into chat mode: {leaked}"

    def test_all_write_tools_in_cowriter_mode(self):
        registry = get_tool_registry()
        cowriter_tools = get_filtered_tools(registry, mode="cowriter")
        cowriter_names = set(cowriter_tools.keys())
        missing = WRITE_TOOLS - cowriter_names
        assert not missing, f"Write tools missing in cowriter mode: {missing}"

    def test_chat_mode_still_reaches_every_read_tool(self):
        registry = get_tool_registry()
        chat_names = set(get_filtered_tools(registry, mode="chat"))
        assert READ_ONLY_TOOLS <= chat_names


# ── ID Dependency Marker in parameter schemas ──────────────────────────────

class TestIdDependencyMarkersInSchemas:
    """Tools that consume an entity ID must name the source read/list tool in
    the **parameter description**, so the AI knows to call it first."""

    ID_SOURCE = [
        # 读：读一个节点 → 也消费 ID
        ("read_chapter_content", "chapter_id", "read_chapters"),
        ("read_character", "character_id", "list_characters"),
        ("read_relation", "relation_id", "list_relations"),
        # 写：内容（只有一条路）
        ("call_writer_agent", "chapter_id", "read_chapters"),
        # 写：章
        ("create_chapter", "act_id", "read_acts"),
        ("update_chapter", "chapter_id", "read_chapters"),
        ("move_chapter", "chapter_id", "read_chapters"),
        ("delete_chapter", "chapter_id", "read_chapters"),
        # 写：幕
        ("update_act", "act_id", "read_acts"),
        ("move_act", "act_id", "read_acts"),
        ("delete_act", "act_id", "read_acts"),
        # 写：人物与关系
        ("update_character", "character_id", "list_characters"),
        ("delete_character", "character_id", "list_characters"),
        ("create_relation", "character_id", "list_characters"),
        ("update_relation", "relation_id", "list_relations"),
        ("delete_relation", "relation_id", "list_relations"),
        # 写：伏笔
        ("create_setup", "planted_in", "read_chapters"),
        ("update_setup", "setup_id", "list_setups"),
        ("delete_setup", "setup_id", "list_setups"),
        # 写作侧
        ("call_writer_agent", "chapter_id", "read_chapters"),
    ]

    @pytest.mark.parametrize(("tool", "param", "source"), ID_SOURCE)
    def test_param_description_names_the_id_source(self, tool, param, source):
        prop = get_tool_registry()[tool].meta.parameters["properties"][param]
        desc = prop.get("description", "")
        assert source in desc, \
            f"{tool}.{param} 的参数描述没有点名 ID 来源 {source!r}：{desc[:200]}"

    @pytest.mark.parametrize(("tool", "param", "source"), ID_SOURCE)
    def test_param_description_uses_the_来自_wording(self, tool, param, source):
        """箭头是从参数描述里正则抓出来的——措辞不能漂。"""
        prop = get_tool_registry()[tool].meta.parameters["properties"][param]
        assert "来自" in prop.get("description", ""), f"{tool}.{param}"

    def test_the_rendered_tool_reference_shows_the_arrow(self):
        """模型真正看到的提示行里必须出现 `param ← 读取工具`。"""
        text = get_tool_descriptions(get_tool_registry())
        assert "chapter_id ← read_chapters" in text
        assert "character_id ← list_characters" in text
        assert "act_id ← read_acts" in text
        assert "relation_id ← list_relations" in text
        assert "setup_id ← list_setups" in text


# ── Error Message Tests ────────────────────────────────────────────────────

class TestMissingParamErrorGuidesToListTool:
    """When a tool call fails due to missing ID, the error message must
    explicitly tell the AI which list tool to call."""

    ID_MAPPING: list[tuple[str, str]] = [
        ("chapter_id",   "read_chapters"),
        ("act_id",       "read_acts"),
        ("character_id", "list_characters"),
        ("relation_id",  "list_relations"),
        ("setup_id",     "list_setups"),
    ]

    @pytest.mark.parametrize(("param_name", "expected_list_tool"), ID_MAPPING)
    def test_error_mentions_specific_list_tool(self, param_name,
                                                expected_list_tool):
        result = BaseTool._missing_param(param_name)
        assert expected_list_tool in result.error, \
            f"_missing_param('{param_name}').error should mention "\
            f"'{expected_list_tool}', got: {result.error[:200]}"

    @pytest.mark.parametrize(("param_name", "expected_list_tool"), ID_MAPPING)
    def test_correction_hint_names_the_read_tool(self, param_name,
                                                 expected_list_tool):
        """correction_hint 必须点名具体工具，而不是只说「先使用读取工具」。"""
        result = BaseTool._missing_param(param_name)
        assert result.correction_hint is not None
        assert expected_list_tool in result.correction_hint, result.correction_hint

    def test_unknown_param_still_gets_a_chinese_error(self):
        result = BaseTool._missing_param("unknown_field")
        assert not result.success
        assert "unknown_field" in result.error


# ── Interceptor / Mode Gate Tests ──────────────────────────────────────────

class TestInterceptorModeGate:
    def test_chat_mode_blocks_all_write_tools(self):
        registry = get_tool_registry()
        for tool_name in WRITE_TOOLS:
            tool_calls = [(tool_name, {"dummy": "param"}, "call_001")]
            result = apply_interceptors(
                tool_calls, mode="chat", tools_registry=registry,
            )
            assert result.blocked, \
                f"'{tool_name}' should be blocked in chat mode"
            assert tool_name in result.blocked_tools

    def test_chat_mode_allows_read_and_list_tools(self):
        registry = get_tool_registry()
        for tool_name in READ_ONLY_TOOLS:
            tool_calls = [(tool_name, {}, "call_001")]
            result = apply_interceptors(
                tool_calls, mode="chat", tools_registry=registry,
            )
            assert not result.blocked, \
                f"Read tool '{tool_name}' should NOT be blocked in chat"

    def test_cowriter_mode_does_not_block_write_tools(self):
        registry = get_tool_registry()
        for tool_name in WRITE_TOOLS:
            tool_calls = [(tool_name, {"dummy": "param"}, "call_001")]
            result = apply_interceptors(
                tool_calls, mode="cowriter", tools_registry=registry,
            )
            assert not result.blocked, \
                f"Write tool '{tool_name}' blocked in cowriter mode"


# ─── Complete "list first, then write" scenario simulation ─────────────────

class TestListThenWriteScenario:
    """Validates the ID dependency chain at the tool meta level:
    list tool provides 'id' → write tool consumes it as *-id param."""

    # Write tools that CREATE new entities don't need existing IDs
    TOOLS_WITHOUT_ID_REQUIREMENT = {
        "create_character", "create_act", "create_project_from_material",
        "update_project",
    }

    def test_required_id_params_exist(self):
        """Every write tool that references an existing entity has at
        least one *-id in its 'required' list."""
        registry = get_tool_registry()
        id_params = {"act_id", "chapter_id", "character_id", "relation_id",
                     "setup_id", "source_id", "target_id", "planted_in",
                     "pay_off_in", "project_id"}

        for name in WRITE_TOOLS:
            if name in self.TOOLS_WITHOUT_ID_REQUIREMENT:
                continue
            tool = registry.get(name)
            assert tool is not None, f"Tool '{name}' not in registry"
            required = tool.meta.parameters.get("required", [])
            has_required_id = any(p in required for p in id_params)
            assert has_required_id, \
                f"'{name}' ('{tool.meta.description[:60]}') needs "\
                f"an ID param in 'required': got {required}"

    def test_list_tools_never_write_operations(self):
        registry = get_tool_registry()
        for name in LIST_TOOLS:
            tool = registry.get(name)
            assert tool is not None, name
            assert not tool.is_write_operation, \
                f"List tool '{name}' should NOT be a write operation"

    def test_read_chapters_output_feeds_update_chapter(self):
        """read_chapters → update_chapter chain."""
        update_ch = get_tool_registry()["update_chapter"]
        required = update_ch.meta.parameters.get("required", [])
        assert "chapter_id" in required, \
            f"update_chapter needs chapter_id, got {required}"

    def test_read_chapters_output_feeds_the_writer_agent(self):
        """read_chapters → call_writer_agent 链。"""
        tool = get_tool_registry()["call_writer_agent"]
        required = tool.meta.parameters.get("required", [])
        assert "chapter_id" in required, \
            f"call_writer_agent needs chapter_id, got {required}"
        assert "action" in tool.meta.parameters["properties"]

    def test_list_setups_scope_param_is_status(self):
        """list_setups 是唯一带筛选参数的名录工具：只看 open / paid / dropped。"""
        props = get_tool_registry()["list_setups"].meta.parameters["properties"]
        assert props["status"]["enum"] == ["open", "paid", "dropped"]

    def test_range_and_pagination_params_exist(self):
        registry = get_tool_registry()
        scoping = {
            "read_chapters": "chapter_from",
            "read_chapter_content": "content_offset",
        }
        for tool_name, scope_param in scoping.items():
            props = registry[tool_name].meta.parameters.get("properties", {})
            assert scope_param in props, \
                f"'{tool_name}' should accept '{scope_param}', " \
                f"got props={list(props.keys())}"

    def test_project_scoped_tools_have_no_project_filter(self):
        """项目身份由会话注入：这些工具不接受按项目筛选的参数。"""
        registry = get_tool_registry()
        for name in ("list_characters", "list_relations", "list_setups",
                     "read_progress", "read_acts", "read_project"):
            props = registry[name].meta.parameters.get("properties", {})
            assert "project_id" not in props, name

    def test_read_chapters_provides_window_ids(self):
        """read_chapters is the range-read entry point — returns chapters
        with global_order, act_id, goal; registered as a non-write tool."""
        registry = get_tool_registry()
        assert "read_chapters" in registry
        assert not registry["read_chapters"].is_write_operation
        props = registry["read_chapters"].meta.parameters.get("properties", {})
        assert {"chapter_from", "chapter_to"} <= set(props)
        chat_tools = get_filtered_tools(registry, mode="chat")
        assert "read_chapters" in chat_tools
        cowriter_tools = get_filtered_tools(registry, mode="cowriter")
        assert "read_chapters" in cowriter_tools

    def test_create_character_then_update_character(self):
        """Simulate the full lifecycle: create → list → update.
        This verifies the chain at a meta level without DB calls."""
        registry = get_tool_registry()

        create = registry["create_character"]
        create_req = create.meta.parameters.get("required", [])
        assert "name" in create_req  # create needs name, not ID
        assert "character_id" not in create_req

        update = registry["update_character"]
        update_req = update.meta.parameters.get("required", [])
        assert "character_id" in update_req  # update needs existing ID

    def test_create_chapter_then_write_it(self):
        """建章 → 写正文的完整链条。

        链条到写正文为止：没有"再写回摘要"这一步了——下一章直接读这一章的
        结尾原文（迁移 0022 删掉了「本章写成了什么」）。
        """
        registry = get_tool_registry()
        create_req = registry["create_chapter"].meta.parameters["required"]
        assert set(create_req) == {"act_id", "chapters"}
        assert "chapter_id" in registry["call_writer_agent"].meta.parameters["required"]

        update_props = registry["update_chapter"].meta.parameters["properties"]
        assert {"title", "goal", "status"} <= set(update_props)
        assert "result" not in update_props, "「本章写成了什么」已删除（迁移 0022）"
        assert update_props["status"]["enum"] == ["draft", "revising", "final"]
