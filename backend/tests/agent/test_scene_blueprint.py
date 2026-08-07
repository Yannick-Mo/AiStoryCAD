"""Regression tests for the scene-blueprint discipline system.

Covers: prompt engineering surfaces (system.yaml / writer.yaml /
project_creator materials / cowriter rules), the new tool
``sync_scene_blueprint`` registration, and the backend scene-sync
fallback logic (never loses data on LLM failure).
"""
from __future__ import annotations

import asyncio
import inspect


def test_sync_scene_blueprint_registered_and_cowriter_only():
    from app.agent.tools import get_tool_registry
    from app.agent.tool_filter import COWRITER_TOOLS, READ_ONLY_TOOLS

    registry = get_tool_registry()
    assert "sync_scene_blueprint" in registry
    assert "sync_scene_blueprint" not in READ_ONLY_TOOLS
    assert "sync_scene_blueprint" in COWRITER_TOOLS


def test_verify_tool_registry_no_drift():
    from app.agent.tools import get_tool_registry
    from app.agent.tool_filter import verify_tool_registry

    assert verify_tool_registry(get_tool_registry()) == []


def test_sync_scene_blueprint_meta_links_list_scenes():
    from app.agent.tools import get_tool_registry

    tool = get_tool_registry()["sync_scene_blueprint"]
    assert "list_scenes" in tool.meta.description
    params = tool.meta.parameters["properties"]
    assert "scene_id" in params and "list_scenes" in params["scene_id"]["description"]


def test_read_scene_meta_mentions_list_scenes_and_include_content():
    from app.agent.tools import get_tool_registry

    tool = get_tool_registry()["read_scene"]
    assert "list_scenes" in tool.meta.description
    assert "include_content" in tool.meta.description
    props = tool.meta.parameters["properties"]
    assert "include_content" in props


def test_loop_base_sections_include_blueprint_discipline():
    import app.agent.loop as loop_mod

    src = inspect.getsource(loop_mod)
    assert "blueprint_discipline" in src
    assert "sync_scene_blueprint" in src  # cowriter writing rules


def test_system_yaml_has_blueprint_discipline_section():
    from app.agent.prompts.builder import get_prompt_builder

    builder = get_prompt_builder()
    assert "blueprint_discipline" in builder.cacheable_sections
    text = builder.get_static_section("blueprint_discipline")
    assert "sync_scene_blueprint" in text
    assert "【结尾状态】" in text


def test_writer_yaml_has_blueprint_discipline():
    from app.agent.prompts import PromptLoader

    tpl = PromptLoader().load("writer")
    assert tpl is not None
    rendered = tpl.render(
        persona="你是一个作家",
        project_title="测试",
        scene_title="测试场景",
        scene_summary="测试蓝图",
        chapter_sort_order=1,
        chapter_title="第一章",
        act_name="第一幕",
        chapter_goal="章蓝图",
        genre="奇幻",
        pov_character_name="小明",
        action="write",
    )
    assert "蓝图纪律" in rendered
    assert "场景蓝图" in rendered
    assert "【节拍】" in rendered or "关键信息" in rendered


def test_project_creator_prompts_are_blueprint_format():
    from app.agent.utils import load_project_prompt

    structure = load_project_prompt("material_structure")
    assert "【章核心】" in structure
    assert "【预期节拍】" in structure
    assert "【结尾钩】" in structure

    scenes = load_project_prompt("material_scenes")
    assert "【目标】" in scenes
    assert "【节拍】" in scenes
    assert "【结尾状态】" in scenes
    assert "不依赖任何正文" in scenes


def test_scene_sync_prompt_renders_and_has_template():
    from app.agent.prompts import render_prompt

    rendered = render_prompt(
        "scene_sync",
        scene_title="酒吧偶遇",
        content="正文",
        scene_summary="",
        chapter_goal="",
        chapter_title="",
        previous_scene_summary="",
        pov_character_name="老陈",
        scene_setting="酒馆",
        scene_time="傍晚",
        genre="悬疑",
    )
    assert rendered
    assert "【目标】" in rendered
    assert "summary" in rendered
    assert "quality_score" in rendered


def test_scene_sync_llm_failure_keeps_original_summary():
    from app.agent.agents.scene_sync import run_scene_sync

    class BrokenClient:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("middle LLM down")

    result = asyncio.run(
        run_scene_sync(BrokenClient(), {"content": "正文" * 100, "scene_summary": "旧蓝图"})
    )
    assert result.summary == "旧蓝图"
    assert result.quality_score == 5


def test_scene_sync_no_content_skips():
    from app.agent.agents.scene_sync import run_scene_sync

    class UnusedClient:
        async def chat(self, messages, **kwargs):
            raise AssertionError("must not be called")

    result = asyncio.run(
        run_scene_sync(UnusedClient(), {"content": "", "scene_summary": "蓝图"})
    )
    assert result.summary == "蓝图"
    assert "无需同步" in result.quality_brief


def test_scene_sync_score_clamped_to_0_10():
    from app.agent.agents.scene_sync import _clamp_score

    assert _clamp_score("13") == 10
    assert _clamp_score(-2) == 0
    assert _clamp_score(7) == 7
    assert _clamp_score("abc") == 5
