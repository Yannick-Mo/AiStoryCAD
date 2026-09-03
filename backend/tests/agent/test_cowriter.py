"""Tests for system prompt building via SystemPromptBuilder (replaces CoWriterMode)."""
import json
import pytest
from app.agent.prompts.builder import get_prompt_builder


class TestSystemPromptBuilder:
    """Verify that builder returns correctly assembled prompts."""

    def test_basic_static_sections_include_identity(self):
        builder = get_prompt_builder()
        prompt = builder.build(["identity", "output_style"])
        # The identity section should mention AiStoryCAD or similar
        assert len(prompt) > 50
        assert "AiStoryCAD" in prompt or "故事" in prompt or "小说" in prompt

    def test_tool_usage_section(self):
        builder = get_prompt_builder()
        prompt = builder.build(["tool_usage"])
        assert "工具" in prompt or "tool" in prompt.lower()

    def test_unknown_section_is_skipped(self):
        builder = get_prompt_builder()
        prompt = builder.build(["identity", "nonexistent_section_xyz"])
        assert "AiStoryCAD" in prompt or "故事" in prompt or "小说" in prompt

    def test_empty_sections_list(self):
        builder = get_prompt_builder()
        prompt = builder.build([])
        assert prompt == ""

    def test_cacheable_sections_come_from_static_cache(self):
        builder = get_prompt_builder()
        cacheable = builder.cacheable_sections
        for name in cacheable:
            static = builder.get_static_section(name)
            assert isinstance(static, str)

    def test_dynamic_sections_are_rendered(self):
        builder = get_prompt_builder()
        dynamic = builder.dynamic_sections
        for name in dynamic:
            # Rendering with empty context should not raise
            result = builder.render_dynamic_section(name)
            assert isinstance(result, str)
