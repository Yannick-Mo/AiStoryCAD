"""蓝图规范的唯一来源，以及各处引用的一致性。

字段与写法曾经散落在七八处（system.yaml / cowriter.yaml / loop.py / 工具描述 /
material_*），漂移的代价很实在：``update_chapter`` 漏掉了整份字段名，写作侧却写着
「蓝图是唯一剧本、不得擅自改动」，于是模型把蓝图逐条翻译成段落。

现在规格只定义在 :mod:`app.agent.blueprint`，这里断言每个消费方都引用到了它。
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.agent.blueprint import (
    CHAPTER_BLUEPRINT_FIELDS,
    SCENE_BLUEPRINT_FIELDS,
    SCENE_FACT_FIELDS,
    chapter_hint,
    lint,
    lint_note,
    render_spec,
    scene_hint,
)
from app.agent.prompts import PromptLoader, render_prompt
from app.agent.utils import load_project_prompt


def _structure_prompt() -> str:
    return load_project_prompt("material_structure").format(
        genre="悬疑", tone="克制", plot_summary="铺梗", world_elements="小镇",
        blueprint_spec=render_spec("chapter"),
    )


def _scenes_prompt() -> str:
    return load_project_prompt("material_scenes").format(
        act_name="第一幕", chapter_title="归乡", chapter_goal="【章核心】g",
        characters_raw_text="老陈", world_elements="小镇",
        blueprint_spec=render_spec("scene"),
    )


def _sync_prompt() -> str:
    return render_prompt(
        "scene_sync", scene_title="酒吧偶遇", content="正文" * 20,
        scene_summary="旧蓝图", chapter_goal="章蓝图", chapter_title="归乡",
        previous_scene_summary="前一场", pov_character_name="老陈",
        scene_setting="酒馆", scene_time="傍晚", genre="悬疑",
        blueprint_spec=render_spec("sync"),
    )


def _example_value(prompt: str, key: str) -> str:
    match = re.search(r'"%s": "(.*?)"' % key, prompt, re.S)
    assert match, f"模板里找不到 {key} 的示例"
    return match.group(1)


class TestSpecSurface:
    def test_spec_is_free_of_template_braces(self):
        """规范会被注入 .format() 模板与 Jinja2 模板，花括号会炸掉渲染。"""
        for kind in ("all", "chapter", "scene", "sync"):
            text = render_spec(kind)
            assert text.strip()
            assert "{" not in text and "}" not in text

    def test_spec_teaches_causal_arrows(self):
        assert "A → B" in render_spec("all")
        assert "A → B" in render_spec("sync")

    def test_spec_names_every_authoring_field(self):
        spec = render_spec("all")
        for field in CHAPTER_BLUEPRINT_FIELDS + SCENE_BLUEPRINT_FIELDS:
            assert f"【{field}】" in spec, field

    def test_sync_spec_names_the_fact_fields(self):
        spec = render_spec("sync")
        for field in SCENE_FACT_FIELDS:
            assert f"【{field}】" in spec, field

    def test_spec_separates_plan_from_facts(self):
        """回写是「计划 → 事实」的替换，不是把两版叠起来。"""
        sync = render_spec("sync")
        assert "计划态字段（【目标】【因果链】【本场基调】）不再保留" in sync
        assert "事实档案是**替换**" in sync

    def test_no_negative_boundary_field(self):
        """蓝图不写「不涉及」这类否定边界。

        提示词给写作 Agent 的否定清单会被它当成内容去处理（「不出现 X」反而容易让它
        在正文里提到 X），所以边界靠「只写该写的」表达。
        """
        assert "不涉及" not in CHAPTER_BLUEPRINT_FIELDS + SCENE_BLUEPRINT_FIELDS
        assert "【不涉及】" not in render_spec("all")
        assert "否定式的边界" in render_spec("all")

    def test_hints_carry_every_field(self):
        for field in CHAPTER_BLUEPRINT_FIELDS:
            assert f"【{field}】" in chapter_hint()
        for field in SCENE_BLUEPRINT_FIELDS:
            assert f"【{field}】" in scene_hint()


class TestPromptSurfaces:
    def test_material_structure_injects_the_spec(self):
        prompt = _structure_prompt()
        assert "{blueprint_spec}" not in prompt
        assert "A → B" in prompt
        for field in CHAPTER_BLUEPRINT_FIELDS:
            assert f"【{field}】" in prompt, field
        assert "【预期节拍】" not in prompt

    def test_material_scenes_injects_the_spec(self):
        prompt = _scenes_prompt()
        assert "{blueprint_spec}" not in prompt
        for field in SCENE_BLUEPRINT_FIELDS:
            assert f"【{field}】" in prompt, field
        assert "【节拍】" not in prompt

    def test_scene_sync_injects_the_spec(self):
        prompt = _sync_prompt()
        assert "{{ blueprint_spec }}" not in prompt
        assert "事实档案" in prompt
        for field in SCENE_FACT_FIELDS:
            assert f"【{field}】" in prompt, field

    def test_loop_injects_the_authoring_spec(self):
        import app.agent.loop as loop_mod

        src = inspect.getsource(loop_mod)
        assert 'render_spec("all")' in src

    def test_blueprint_discipline_points_at_the_spec(self):
        from app.agent.prompts.builder import get_prompt_builder

        text = get_prompt_builder().get_static_section("blueprint_discipline")
        assert "创作蓝图规范" in text
        assert "sync_scene_blueprint" in text
        # 字段清单不再重复出现在这一节里（单一来源）
        assert "【章核心】" not in text
        assert "【预期节拍】" not in text

    def test_cowriter_points_at_the_spec(self):
        from pathlib import Path
        from app.agent.prompts.builder import get_prompt_builder  # noqa: F401

        p = (Path(__file__).parent.parent.parent
             / "app" / "agent" / "prompts" / "cowriter.yaml")
        src = p.read_text(encoding="utf-8")
        assert "创作蓝图规范" in src
        assert "【预期节拍】" not in src


class TestExamplesTeachTheRightThing:
    """提示词里的示例是模型最强的模仿对象——示例合规比规则更管用。"""

    def test_structure_example_uses_arrows_without_banned_words(self):
        goal = _example_value(_structure_prompt(), "goal")
        assert "→" in goal
        assert lint(goal) == []

    def test_scenes_example_uses_arrows_without_banned_words(self):
        summary = _example_value(_scenes_prompt(), "summary")
        assert summary.count("→") >= 3
        assert lint(summary) == []

    def test_banned_words_are_named_once_in_the_ban_list(self):
        """禁用词只允许出现在「不要写这些」的清单里。"""
        spec = render_spec("all")
        ban_block = spec.split("## 绝不写进蓝图")[1].split("## ")[0]
        for word in ("笑点", "爆点", "拉满"):
            assert word in ban_block
            assert spec.replace(ban_block, "").count(word) == 0


class TestToolDescriptionsUseTheHints:
    @pytest.mark.parametrize("tool,param,hint", [
        ("create_scene", "summary", scene_hint()),
        ("update_scene", "summary", scene_hint()),
        ("create_chapter", "goal", chapter_hint()),
        ("set_chapter_goal", "goal", chapter_hint()),
        ("update_chapter", "goal", chapter_hint()),
    ])
    def test_param_description_comes_from_the_hint(self, tool, param, hint):
        from app.agent.tools import get_tool_registry

        reg = get_tool_registry()
        got = reg[tool].meta.parameters["properties"][param]["description"]
        assert got == hint

    def test_tools_report_banned_wording_back_to_the_model(self):
        from app.agent.tools import get_tool_registry

        reg = get_tool_registry()
        for name in ("create_scene", "update_scene", "create_chapter",
                     "set_chapter_goal", "update_chapter"):
            src = inspect.getsource(type(reg[name]))
            assert "lint_note" in src, name


class TestWriterSideTreatsBlueprintAsIntent:
    def test_writer_rules_are_event_level_not_verbatim(self):
        prompt = render_prompt(
            "writer", persona="作家", project_title="T", scene_title="S",
            scene_summary="蓝图", chapter_number=1, chapter_title="C",
            chapter_goal="G", genre="悬疑", pov_character_name="小", action="write",
        )
        assert "事件级忠实" in prompt
        assert "唯一剧本" not in prompt
        assert "不得擅自改动" not in prompt

    def test_persona_writer_drops_the_script_wording(self):
        persona = PromptLoader().load("persona_writer").render()
        assert "唯一剧本" not in persona
        assert "不规定「怎么写」" in persona


class TestLint:
    def test_flags_evaluative_wording(self):
        hits = lint("【因果链】主角笑点拉满 → 读者爆点")
        assert "笑点" in hits and "拉满" in hits and "爆点" in hits

    def test_passes_a_clean_blueprint(self):
        assert lint("【因果链】柳父登门赔罪 → 主角当场同意退婚") == []

    def test_note_is_empty_when_clean(self):
        assert lint_note("【目标】老陈得知有人要杀他") == ""
        assert "创作蓝图规范" in lint_note("【目标】笑点拉满")