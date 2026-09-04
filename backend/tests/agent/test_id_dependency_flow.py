"""Tests for the "list IDs first, then call tool" business flow.

Verifies the complete lifecycle of ID-dependent tool calling:
1. Tool filter: list tools available in both modes; write tools only in cowriter
2. ← markers in descriptions tell AI which list tool to call first
3. Error messages for missing params guide AI back to the right list tool
4. Mode gate (interceptor) blocks write tools in chat mode
"""

import pytest
from app.agent.tools import get_tool_registry, get_filtered_tools
from app.agent.tools.base import BaseTool
from app.agent.tool_filter import READ_ONLY_TOOLS, COWRITER_TOOLS
from app.agent.interceptors import apply_interceptors


# ── Tool sets ───────────────────────────────────────────────────────────────

LIST_TOOLS = {"list_characters", "list_relations", "list_edges"}

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


# ── ID Dependency Marker in Descriptions Tests ─────────────────────────────

class TestIdDependencyMarkersInDescriptions:
    """Tools that consume an entity ID must reference the source
    read/list tool in meta.description so the AI knows to call it first.

    Known gaps (descriptions missing ← marker):
      * create_chapter / create_edge — should name the act/chapter source
      * update_character, delete_character — should mention list_characters
      * delete_relation            — should mention list_characters
    """

    def test_write_scene_content_links_id_source(self):
        desc = get_tool_registry()["write_scene_content"].meta.description
        assert "read_chapter" in desc, desc[:200]

    def test_read_scene_links_id_source(self):
        desc = get_tool_registry()["read_scene"].meta.description
        assert "read_chapter" in desc, desc[:200]

    def test_delete_scene_links_id_source(self):
        desc = get_tool_registry()["delete_scene"].meta.description
        assert "read_chapter" in desc, desc[:200]

    def test_read_chapter_links_read_chapters(self):
        desc = get_tool_registry()["read_chapter"].meta.description
        assert "read_chapters" in desc, desc[:200]

    def test_update_chapter_links_read_chapters(self):
        desc = get_tool_registry()["update_chapter"].meta.description
        assert "read_chapters" in desc, desc[:200]

    def test_delete_chapter_links_read_chapters(self):
        desc = get_tool_registry()["delete_chapter"].meta.description
        assert "read_chapters" in desc, desc[:200]

    def test_delete_act_links_read_chapters(self):
        desc = get_tool_registry()["delete_act"].meta.description
        assert "read_chapters" in desc, desc[:200]

    # ── Known gaps (descriptions lack ← markers) ──────────────────────

    def test_update_scene_should_link_id_source(self):
        """GAP: description is '更新场景内容、标题、POV、地点、时间、梗概等'
        — should mention read_chapter as the ID source."""
        desc = get_tool_registry()["update_scene"].meta.description
        assert "read_chapter" in desc, \
            f"MISSING ← marker in update_scene: {desc[:200]}"

    def test_create_scene_should_link_read_chapters(self):
        """GAP: description mentions '章节ID' generically
        — should name read_chapters as the source."""
        desc = get_tool_registry()["create_scene"].meta.description
        assert "read_chapters" in desc, \
            f"MISSING ← marker in create_scene: {desc[:200]}"

    def test_create_chapter_should_link_read_chapters(self):
        """GAP: description mentions '幕ID' generically
        — should name read_chapters as the source."""
        desc = get_tool_registry()["create_chapter"].meta.description
        assert "read_chapters" in desc, \
            f"MISSING ← marker in create_chapter: {desc[:200]}"

    def test_update_character_should_link_list_characters(self):
        """GAP: description says '需提供角色ID' generically
        — should name list_characters as the source."""
        desc = get_tool_registry()["update_character"].meta.description
        assert "list_characters" in desc, \
            f"MISSING ← marker in update_character: {desc[:200]}"

    def test_delete_character_should_link_list_characters(self):
        """GAP: no ← marker in delete_character description."""
        desc = get_tool_registry()["delete_character"].meta.description
        assert "list_characters" in desc, \
            f"MISSING ← marker in delete_character: {desc[:200]}"

    def test_create_edge_should_link_read_chapters(self):
        """GAP: create_edge needs source_id/target_id but description
        doesn't name read_chapters as their source."""
        desc = get_tool_registry()["create_edge"].meta.description
        assert "read_chapters" in desc, \
            f"MISSING ← marker in create_edge: {desc[:200]}"

    def test_delete_relation_should_link_list_relations(self):
        """delete_relation takes relation_id, which comes from list_relations."""
        desc = get_tool_registry()["delete_relation"].meta.description
        assert "relation_id" in desc and "list_relations" in desc, \
            f"MISSING ← marker in delete_relation: {desc[:200]}"


# ── Error Message Tests ────────────────────────────────────────────────────

class TestMissingParamErrorGuidesToListTool:
    """When a tool call fails due to missing ID, the error message must
    explicitly tell the AI which list tool to call."""

    ID_MAPPING: list[tuple[str, str]] = [
        ("chapter_id",   "read_chapters"),
        ("scene_id",     "read_chapter"),
        ("character_id", "list_characters"),
        ("edge_id",      "list_edges"),
    ]

    @pytest.mark.parametrize("param_name,expected_list_tool", ID_MAPPING)
    def test_error_mentions_specific_list_tool(self, param_name,
                                                expected_list_tool):
        result = BaseTool._missing_param(param_name)
        assert expected_list_tool in result.error, \
            f"_missing_param('{param_name}').error should mention "\
            f"'{expected_list_tool}', got: {result.error[:200]}"

    @pytest.mark.parametrize("param_name,expected_list_tool", ID_MAPPING)
    def test_correction_hint_is_too_generic(self, param_name,
                                             expected_list_tool):
        """Known gap: correction_hint says 'list_* 系列' instead of
        naming the specific tool like the error field does."""
        result = BaseTool._missing_param(param_name)
        assert result.correction_hint is not None
        expected_specific = expected_list_tool in result.correction_hint
        if not expected_specific:
            pytest.xfail(
                f"correction_hint is generic: '{result.correction_hint}' "
                f"— should mention '{expected_list_tool}'"
            )


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
        "set_chapter_goal",
        "update_project",
    }

    def test_required_id_params_exist(self):
        """Every write tool that references an existing entity has at
        least one *-id in its 'required' list."""
        registry = get_tool_registry()
        id_params = {"act_id", "chapter_id", "scene_id", "character_id",
                     "source_id", "target_id", "edge_id",
                     "relation_id", "project_id"}

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
            assert not tool.is_write_operation, \
                f"List tool '{name}' should NOT be a write operation"

    def test_read_chapter_output_feeds_update_chapter(self):
        """read_chapters/read_chapter → update_chapter chain."""
        update_ch = get_tool_registry()["update_chapter"]
        required = update_ch.meta.parameters.get("required", [])
        assert "chapter_id" in required, \
            f"update_chapter needs chapter_id, got {required}"

    def test_read_scene_output_feeds_write_scene(self):
        """read_chapter (scenes) → write_scene_content chain."""
        write_sc = get_tool_registry()["write_scene_content"]
        required = write_sc.meta.parameters.get("required", [])
        assert "scene_id" in required, \
            f"write_scene_content needs scene_id, got {required}"

    def test_list_tools_accept_scope_params(self):
        """Most list/range tools accept a scope param to narrow results.
        Known gap: list_characters and list_edges accept no params."""
        registry = get_tool_registry()
        scoping = {
            "list_relations":  "character_id",
            "read_chapters":   "chapter_from",
            "read_recent":     "kind",
        }
        for tool_name, scope_param in scoping.items():
            tool = registry[tool_name]
            props = tool.meta.parameters.get("properties", {})
            assert scope_param in props, \
                f"'{tool_name}' should accept '{scope_param}', " \
                f"got props={list(props.keys())}"

    @pytest.mark.xfail(reason="Known gap: list_characters accepts no scope params (uses session context)")
    def test_list_characters_accepts_no_scope_param(self):
        """Known gap: list_characters has no project_id param.
        It implicitly returns all characters via session context."""
        tool = get_tool_registry()["list_characters"]
        props = tool.meta.parameters.get("properties", {})
        assert "project_id" in props, \
            f"GAP: list_characters accepts no scope params: {list(props.keys())}"

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
