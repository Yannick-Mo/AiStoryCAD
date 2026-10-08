"""Tests for write tools logic — tool_filter write set."""

from app.agent.tool_filter import COWRITER_TOOLS, READ_ONLY_TOOLS


class TestWriteTools:
    """Verify the write/read tool classification in tool_filter.py."""

    def test_known_write_tools(self):
        assert "update_chapter" in COWRITER_TOOLS
        assert "call_writer_agent" in COWRITER_TOOLS, "写正文的唯一入口必须对协作模式可见"
        assert "create_character" in COWRITER_TOOLS

    def test_prose_write_tools_are_gone(self):
        """write_chapter / continue_chapter 已删除，别让它们回来。"""
        for gone in ("write_chapter", "continue_chapter"):
            assert gone not in COWRITER_TOOLS, gone

    def test_read_tools_not_in_write(self):
        assert "read_project" in READ_ONLY_TOOLS
        assert "search_knowledge" in READ_ONLY_TOOLS
        assert "read_project" not in COWRITER_TOOLS
        assert "search_knowledge" not in COWRITER_TOOLS

    def test_all_cowriter_tools_follow_naming(self):
        """Verify cowriter tools don't start with read-only prefixes."""
        for tool in COWRITER_TOOLS:
            assert not tool.startswith("read_"), f"Read tool in cowriter tools: {tool}"
            assert not tool.startswith("search_"), f"Search tool in cowriter tools: {tool}"
            assert not tool.startswith("list_"), f"List tool in cowriter tools: {tool}"
            assert not tool.startswith("analyze_"), f"Analyze tool in cowriter tools: {tool}"
            assert not tool.startswith("check_"), f"Check tool in cowriter tools: {tool}"

    def test_no_overlap(self):
        """Read-only and cowriter tools should be disjoint."""
        overlap = READ_ONLY_TOOLS & COWRITER_TOOLS
        assert not overlap, f"Tools in both sets: {overlap}"
