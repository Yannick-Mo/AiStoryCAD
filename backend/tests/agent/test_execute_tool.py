"""Tests for tool parameter validation and error message consistency."""

import pytest
from app.agent.tools.base import BaseTool, ToolResult


class ValidatableTool(BaseTool):
    name = "test_tool"
    description = "A test tool"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name"},
            "count": {"type": "integer", "description": "Count"},
        },
        "required": ["name"],
    }

    async def run(self, db, **kwargs):
        from app.agent.tools.base import ToolResult
        return ToolResult(success=True, data=kwargs)


class TestRequireParam:
    """Tests for BaseTool._require_param() — the canonical param validator."""

    def test_returns_value_when_present(self):
        val = BaseTool._require_param({"scene_id": "abc-123"}, "scene_id")
        assert val == "abc-123"

    def test_returns_none_when_missing(self):
        assert BaseTool._require_param({}, "scene_id") is None

    def test_returns_none_for_empty_string(self):
        assert BaseTool._require_param({"scene_id": "  "}, "scene_id") is None

    def test_returns_none_for_none_value(self):
        assert BaseTool._require_param({"scene_id": None}, "scene_id") is None

    def test_strips_whitespace(self):
        val = BaseTool._require_param({"name": "  hello  "}, "name")
        assert val == "hello"


class TestMissingParam:
    """Tests for BaseTool._missing_param() — generates ToolResult with hint."""

    def test_known_param_produces_chinese_hint(self):
        result = BaseTool._missing_param("chapter_id")
        assert result.success is False
        assert "chapter_id" in result.error
        assert "list_chapters" in result.error

    def test_scene_id_hint(self):
        result = BaseTool._missing_param("scene_id")
        assert "list_scenes" in result.error

    def test_character_id_hint(self):
        result = BaseTool._missing_param("character_id")
        assert "list_characters" in result.error

    def test_edge_id_hint(self):
        result = BaseTool._missing_param("edge_id")
        assert "list_edges" in result.error

    def test_unknown_param_fallback(self):
        result = BaseTool._missing_param("unknown_field")
        assert result.success is False
        assert "unknown_field" in result.error

    def test_has_correction_hint(self):
        result = BaseTool._missing_param("chapter_id")
        assert result.correction_hint is not None
        assert "chapter_id" in result.correction_hint


class TestNotFound:
    """Tests for BaseTool._not_found() — entity-not-found errors."""

    def test_chapter_not_found(self):
        result = BaseTool._not_found("Chapter")
        assert result.success is False
        assert "章节" in result.error
        assert result.correction_hint is not None

    def test_scene_not_found(self):
        result = BaseTool._not_found("Scene")
        assert "场景" in result.error

    def test_character_not_found(self):
        result = BaseTool._not_found("Character")
        assert "角色" in result.error

    def test_act_not_found(self):
        result = BaseTool._not_found("Act")
        assert "幕" in result.error

    def test_unknown_entity(self):
        result = BaseTool._not_found("UnknownThing")
        assert result.success is False
        assert "UnknownThing" in result.error

    def test_with_extra_context(self):
        result = BaseTool._not_found("Chapter", "源章节ID: abc-123")
        assert "源章节ID: abc-123" in result.error


class TestAlreadyExists:
    """Tests for BaseTool._already_exists() — duplicate entity errors."""

    def test_character_already_exists(self):
        result = BaseTool._already_exists("角色", "张三")
        assert result.success is False
        assert "张三" in result.error
        assert result.correction_hint is not None
        assert "更新工具" in result.correction_hint

    def test_with_existing_id(self):
        result = BaseTool._already_exists("角色", "张三", existing_id="abc-123")
        assert "abc-123" in result.error or "abc-123" in result.correction_hint


class TestPermissionDenied:
    """Tests for BaseTool._permission_denied() — ownership errors."""

    def test_generic_permission(self):
        result = BaseTool._permission_denied()
        assert result.success is False
        assert "权限" in result.error

    def test_entity_permission(self):
        result = BaseTool._permission_denied("场景")
        assert "场景" in result.error


class TestWriteToolsConsistency:
    def test_write_tools_all_have_corresponding_run_methods(self):
        from app.agent.tools import get_tool_registry
        from app.agent.tool_filter import READ_ONLY_TOOLS, COWRITER_TOOLS
        registry = get_tool_registry()
        all_tools = READ_ONLY_TOOLS | COWRITER_TOOLS
        for tool_name in all_tools:
            assert tool_name in registry, f"Tool '{tool_name}' not found in tool registry"


class TestErrorMessagesAreChinese:
    """Verify that tool errors from the base class helpers are in Chinese."""

    def test_not_found_is_chinese(self):
        for entity in ["Act", "Chapter", "Scene", "Character", "Project"]:
            result = BaseTool._not_found(entity)
            # Must contain CJK characters
            assert any('一' <= c <= '鿿' for c in result.error), \
                f"Error for '{entity}' is not in Chinese: {result.error}"

    def test_missing_param_is_chinese(self):
        result = BaseTool._missing_param("chapter_id")
        assert any('一' <= c <= '鿿' for c in result.error)

    def test_already_exists_is_chinese(self):
        result = BaseTool._already_exists("角色", "test")
        assert any('一' <= c <= '鿿' for c in result.error)

    def test_permission_denied_is_chinese(self):
        result = BaseTool._permission_denied("幕")
        assert any('一' <= c <= '鿿' for c in result.error)

    def test_correction_hint_is_chinese(self):
        """correction_hint must be in Chinese for LLM self-correction."""
        for k in ["chapter_id", "scene_id", "character_id", "edge_id"]:
            result = BaseTool._missing_param(k)
            assert result.correction_hint is not None, f"No correction_hint for {k}"
            assert any('一' <= c <= '鿿' for c in result.correction_hint), \
                f"correction_hint for '{k}' is not in Chinese: {result.correction_hint}"
