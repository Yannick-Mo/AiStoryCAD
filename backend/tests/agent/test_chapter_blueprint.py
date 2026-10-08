"""章蓝图（「本章要写什么」）写法说明的唯一来源，以及各处引用的一致性。

蓝图是**一段散文摘要**，不是字段清单：说清这一章要写什么，不检查措辞、不凑字段。
写法只定义在 :mod:`app.agent.blueprint`，这里断言每个消费方都引用到了它。

历史上写法散落在七八处（system.yaml / cowriter.yaml / loop.py / 工具描述 / material_*），
漂移的代价很实在：``update_chapter`` 漏掉了整份字段名，写作侧却写着「蓝图是唯一剧本、
不得擅自改动」，于是模型把蓝图逐条翻译成段落。

**场景蓝图没有了**：场景层已删除，章是最底层、也是正文层。所以 ``render_guide()``
只有一份说明，``scene_hint()`` 与 ``material_scenes.yaml`` 都已删除
（本文件由 ``test_blueprint_spec.py`` 与 ``test_scene_blueprint.py`` 合并而来）。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import app.agent.blueprint as blueprint_mod
from app.agent.blueprint import chapter_hint, render_guide
from app.agent.prompts import PromptLoader, render_prompt
from app.agent.utils import load_project_prompt

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "app" / "agent" / "prompts"


def _chapters_prompt() -> str:
    """素材流水线「逐章写蓝图」那一步的模板，必须能装上这份说明。"""
    return load_project_prompt("material_chapters").format(
        act_name="第一幕",
        act_purpose="把他扣在镇上",
        ending="终局：陈默留下查清旧债",
        spine="主线骨架：奔丧 → 账本缺页 → 姑妈改口 → 留下",
        characters_text="陈默（主角）",
        chapters_text="- 归乡\n- 撕掉的几页",
        blueprint_spec=render_guide(),
    )


def _structure_prompt() -> str:
    return load_project_prompt("material_structure").format(
        genre="悬疑", tone="克制", logline="一个外人的丧事变成他的旧案",
        plot_summary="铺梗", world_elements="小镇",
        ending="他留下", spine="奔丧 → 留下",
    )


def _writer_prompt(**overrides) -> str:
    ctx = dict(
        persona="作家", project_title="T", chapter_title="归乡",
        chapter_number=1, chapter_goal="陈默回乡查旧账", genre="悬疑",
        action="write",
        length_target="3000–4000 字（本书已写 2 章，平均 3500 字）。",
        next_chapter_title="第三章", next_chapter_goal="他决定留下。",
    )
    ctx.update(overrides)
    return render_prompt("writer", **ctx)


def _example_value(prompt: str, key: str) -> str:
    match = re.search(f'"{key}": "(.*?)"', prompt, re.S)
    assert match, f"模板里找不到 {key} 的示例"
    return match.group(1)


# ── 说明本身 ──────────────────────────────────────────────────────────────────

class TestGuideSurface:
    def test_guide_is_free_of_template_braces(self):
        """说明会被注入 .format() 模板与 Jinja2 模板，花括号会炸掉渲染。"""
        text = render_guide()
        assert text.strip()
        assert "{" not in text
        assert "}" not in text

    def test_the_kind_argument_is_gone_and_ignored(self):
        """只有一种蓝图了。``kind`` 已废弃，传什么都不影响返回值。"""
        assert render_guide() == render_guide("chapter") == render_guide("scene") == render_guide("all")

    def test_guide_asks_for_prose_not_fields(self):
        text = render_guide()
        assert "# --- 章蓝图（本章要写什么）---" in text
        assert "把这一章要写的内容概括清楚" in text
        assert "不是三个栏目" in text

    def test_guide_states_the_single_rule(self):
        assert "这句话说的是「发生什么」，还是「该怎么写得好看」？" in render_guide()

    def test_guide_does_not_set_a_word_count(self):
        """不设字数——但必须显式要求「一段话」。

        实测（每版 3 案例 × 多次）：只写「写成一段连贯的话」时，模型有 4/12 个蓝图
        会自己分段并且越写越长；补上「不分段」后 12/12 都是单段，字数也回落到目标区间。
        """
        text = render_guide()
        assert "不设字数" in text
        assert "不分段" in text
        assert "别只写一句话" in text
        assert "不用写「怎么演」" in text
        assert "200" not in text

    def test_guide_has_exactly_one_example_and_it_is_a_chapter_one(self):
        text = render_guide()
        assert "## 例子" in text
        assert "场景蓝图" not in text, "场景蓝图已随场景层删除"
        assert text.count("## 例子") == 1

    def test_guide_carries_no_enforcement_language(self):
        text = render_guide()
        for word in ("必须遵守", "严格按", "不得", "禁止", "最高优先级", "纪律"):
            assert word not in text, word

    def test_guide_tells_the_model_not_to_rewrite_legacy_blueprints(self):
        assert "不用改写" in render_guide()


class TestNoLintMachinery:
    """蓝图不再做措辞合规检查——没有禁用词表，也没有回执提示。"""

    @pytest.mark.parametrize("name", [
        "lint", "lint_note", "BANNED_IN_BLUEPRINT",
        "CHAPTER_BLUEPRINT_FIELDS", "SCENE_BLUEPRINT_FIELDS",
        "render_spec", "CHAPTER_SPEC", "SCENE_SPEC", "scene_hint",
    ])
    def test_blueprint_module_no_longer_exports_it(self, name):
        assert not hasattr(blueprint_mod, name), name

    @pytest.mark.parametrize("tool", [
        "create_chapter", "update_chapter", "write_chapter", "continue_chapter",
    ])
    def test_tools_do_not_report_blueprint_wording(self, tool):
        from app.agent.tools import get_tool_registry

        src = inspect.getsource(type(get_tool_registry()[tool]))
        assert "lint" not in src, tool
        assert "blueprint_note" not in src, tool


# ── 各处引用 ──────────────────────────────────────────────────────────────────

class TestPromptSurfaces:
    def test_material_chapters_injects_the_guide(self):
        prompt = _chapters_prompt()
        assert "{blueprint_spec}" not in prompt
        assert "# --- 章蓝图（本章要写什么）---" in prompt
        assert "## 例子" in prompt
        assert "【章核心】" not in prompt

    def test_material_structure_is_structure_only(self):
        """结构那一步只定幕与章名，蓝图留给下一步逐章写。"""
        prompt = _structure_prompt()
        assert "{blueprint_spec}" not in prompt
        assert "不写正文" in prompt
        assert "也不要写每一章的蓝图" in prompt

    def test_loop_injects_the_guide(self):
        import app.agent.loop as loop_mod

        src = inspect.getsource(loop_mod)
        assert "from app.agent.blueprint import render_guide" in src
        assert "render_guide()" in src
        assert "blueprint_discipline" not in src

    def test_system_yaml_dropped_the_blueprint_discipline_section(self):
        from app.agent.prompts.builder import get_prompt_builder

        builder = get_prompt_builder()
        assert "blueprint_discipline" not in builder.section_names
        assert "blueprint_discipline" not in builder.cacheable_sections
        # 写作工序仍在，并且写成「建章时顺手把蓝图写了」
        usage = builder.get_static_section("tool_usage")
        assert "建章时顺手把「本章要写什么」写了" in usage

    def test_cowriter_points_at_the_guide(self):
        src = (_PROMPTS_DIR / "cowriter.yaml").read_text(encoding="utf-8")
        assert "章蓝图" in src
        assert "《章蓝图》" in src
        assert "没有场景层了" in src
        assert "【因果链】" not in src

    def test_scene_prompts_are_gone(self):
        assert load_project_prompt("material_scenes") == ""
        assert load_project_prompt("material_edges") == ""

    def test_verify_tool_registry_no_drift(self):
        from app.agent.tools import get_tool_registry
        from app.agent.tool_filter import verify_tool_registry

        assert verify_tool_registry(get_tool_registry()) == []


class TestExamplesTeachTheRightThing:
    """提示词里的示例是模型最强的模仿对象——示例是散文，输出才会是散文。"""

    def test_chapter_blueprint_example_is_prose(self):
        goal = _example_value(_chapters_prompt(), "goal")
        assert "【" not in goal
        assert len(goal) > 150

    def test_guide_examples_are_substantial(self):
        """示例比规则更能决定输出长度——示例短了，模型就写短。"""
        block = render_guide().split("## 例子")[1].split("## ")[0]
        good = block.split("**不好**")[0] if "**不好**" in block else block
        assert len(good) > 200

    def test_guide_shows_a_good_example(self):
        example = render_guide().split("## 例子")[1]
        assert "**好**" in example
        assert "验收单" not in example.split("**好**")[0]


class TestToolDescriptionsUseTheHints:
    @pytest.mark.parametrize(("tool", "param_path", "hint"), [
        # create_chapter 一次可以建多章，所以 goal 在 chapters[] 条目里
        ("create_chapter", "properties.chapters.items.properties.goal", chapter_hint()),
        ("update_chapter", "properties.goal", chapter_hint()),
    ])
    def test_param_description_comes_from_the_hint(self, tool, param_path, hint):
        from app.agent.tools import get_tool_registry

        node = get_tool_registry()[tool].meta.parameters
        for step in param_path.split("."):
            node = node[step]
        assert node["description"] == hint

    def test_hint_asks_for_a_paragraph(self):
        hint = chapter_hint()
        assert "一段话" in hint
        assert "【" not in hint
        assert "字左右" not in hint, "工具描述里不该出现字数要求"

    def test_no_tool_exposes_scene_blueprint_fields(self):
        from app.agent.tools import get_tool_registry

        for name, tool in get_tool_registry().items():
            props = tool.meta.parameters.get("properties", {})
            for field in ("summary", "pov_character", "scene_time", "scene_id"):
                assert field not in props, f"{name} 还暴露着场景字段 {field}"


# ── 写作侧怎么用蓝图 ──────────────────────────────────────────────────────────

class TestWriterSideTreatsBlueprintAsIntent:
    def test_writer_rules_are_not_verbatim_translation(self):
        prompt = _writer_prompt()
        assert "蓝图怎么用" in prompt
        assert "不要当验收表" in prompt
        assert "唯一剧本" not in prompt
        assert "不得擅自改动" not in prompt

    def test_persona_writer_drops_the_script_wording(self):
        persona = PromptLoader().load("persona_writer").render()
        assert "唯一剧本" not in persona
        assert "不规定「怎么写」" in persona
        assert "权威分级" not in persona

    def test_persona_is_identity_level_only(self):
        """人格只放身份级信念：操作规则在下文「蓝图怎么用」，两处都写会挤占注意力。"""
        persona = PromptLoader().load("persona_writer").render()
        assert "你写的是**小说**" in persona
        assert "蓝图是**材料**，不是验收表" in persona
        assert "展示不告知" in persona
        assert len(persona) < 700, len(persona)


class TestWriterKnowsHowToUseTheBlueprint:
    def test_this_chapter_is_the_scope(self):
        """作用域写成肯定句：否定清单会让模型刻意回避，反而分散注意力。"""
        prompt = _writer_prompt()
        assert "本章只演本章" in prompt
        assert "不得预告" not in prompt
        assert "不得实现其他场" not in prompt

    def test_the_model_may_fill_blueprint_gaps(self):
        prompt = _writer_prompt()
        assert "蓝图漏了的地方你自己判断" in prompt
        assert "蓝图之外可以长东西" in prompt

    def test_information_may_arrive_indirectly(self):
        prompt = _writer_prompt()
        assert "优先不明说" in prompt
        assert "优先选不明说的那一种" in prompt

    def test_anti_checklist_rules(self):
        prompt = _writer_prompt()
        assert "要点自然落地" in prompt
        for rule in ("设定靠行动显出来", "已知信息直接当已知用", "伏笔就是细节本身",
                     "收在一件具体的东西上"):
            assert rule in prompt, rule
        assert "禁止" not in prompt.split("### 要点自然落地")[1].split("###")[0]

    def test_the_chapter_is_driven_by_character_desire(self):
        assert "什么在挡他" in _writer_prompt()

    def test_length_follows_the_chapter_not_the_blueprint(self):
        prompt = _writer_prompt()
        assert "篇幅由这一章的分量决定，不由蓝图的长短决定" in prompt
        assert "自然而定" not in prompt

    def test_the_viewpoint_lives_in_the_blueprint_not_in_a_field(self):
        """视角没有字段：提示词只能教写手从蓝图里读出来。"""
        prompt = _writer_prompt()
        assert "视角写在章蓝图里" in prompt
        assert "pov_character" not in prompt


class TestThePromptEndsOnTheNovel:
    """收尾锚点：最后读到的一段应该是「这是小说」，而不是一条格式约束。"""

    def test_the_last_section_is_the_anchor(self):
        prompt = _writer_prompt()
        anchor = prompt.rindex("落笔前最后重申一遍")
        # 锚点必须在「怎么落笔」与「输出要求」之后：最后读到的是"这是小说"，
        # 而不是一份规则清单或格式约束。
        assert anchor > prompt.rindex("怎么落笔")
        assert anchor > prompt.rindex("输出要求")
        assert prompt[anchor:].rstrip().endswith("现在，把这一章写出来。")

    def test_anchor_puts_story_first_but_keeps_the_floor(self):
        prompt = _writer_prompt()
        assert "好读才是上限" in prompt
        assert "下限" in prompt
        assert "用小说家的判断写这一章" in prompt

    def test_anchor_frees_the_model_from_checking_while_writing(self):
        prompt = _writer_prompt()
        assert "不是来检查你有没有落实条目" in prompt
        assert "读者心里应该有一件想知道的事" in prompt

    def test_anchor_has_no_denial_list(self):
        prompt = _writer_prompt()
        anchor = prompt[prompt.rindex("落笔前最后重申一遍"):]
        for word in ("不得", "禁止", "不该"):
            assert word not in anchor, word

    async def test_user_message_also_frames_it_as_novel_prose(self):
        from app.agent.agents.writer_agent import WritingAgent

        captured: dict = {}

        class StubClient:
            async def chat(self, messages, **kwargs):
                captured["messages"] = messages

                class _R:
                    content = "正文正文"
                    finish_reason = "stop"

                return _R()

        ctx = {
            "chapter_title": "归乡", "chapter_goal": "陈默回乡查旧账",
            "project_title": "T", "chapter_number": 1, "genre": "悬疑",
        }
        await WritingAgent().run(StubClient(), ctx, "请创作章《归乡》的正文。")
        user = [m for m in captured["messages"] if m.role == "user"][0].content
        assert "你写的是小说正文" in user
        assert "先让它好读" in user
        assert "直接输出正文" in user


class TestWriterLoadsNoSkill:
    """写作链路不加载技能：传进去无效，模板里也没有这个通道。

    技能（active_skills → get_merged_prompts["writing"]）曾拼进写手 prompt，
    占 1963 字 / 19%。移除理由：写手是纯文本生成器，题材取向由 genre 字段、
    项目 global_settings 与蓝图提供；主 Agent 侧仍保留技能。
    """

    def test_passing_skill_guidance_does_nothing(self):
        prompt = _writer_prompt(skill_writing_guidance="爽点模式 A/B/C")
        assert "爽点模式 A/B/C" not in prompt
        assert "类型写作参考" not in prompt

    def test_template_declares_no_skill_channel(self):
        assert "skill_writing_guidance" not in _writer_prompt()

    def test_writer_tool_has_no_skill_machinery(self):
        import app.agent.tools.writer_tool as mod

        src = inspect.getsource(mod)
        for bad in ("skill_writing_guidance", "get_merged_prompts",
                    "skill_engine", "active_skills"):
            assert bad not in src, bad

    def test_executor_stops_injecting_active_skills(self):
        from app.agent.tools.streaming_executor import StreamingToolExecutor

        assert "active_skills" not in inspect.getsource(StreamingToolExecutor)


class TestUserRequirementReachesTheWriter:
    def test_the_users_words_are_rendered(self):
        prompt = _writer_prompt(user_requirement="这一章写成无声的，不要对白")
        assert "用户对这次写作的要求" in prompt
        assert "这一章写成无声的，不要对白" in prompt

    def test_no_requirement_means_no_section(self):
        prompt = _writer_prompt()
        assert "用户对这次写作的要求" not in prompt
        assert "附加写作要求" not in prompt


class TestWriterToolContract:
    """call_writer_agent 的参数契约：只装用户意愿，不装工具会自己加载的数据。"""

    def _tool(self):
        from app.agent.tools import get_tool_registry

        return get_tool_registry()["call_writer_agent"]

    def test_parameters_are_only_three(self):
        params = self._tool().meta.parameters
        assert set(params["properties"]) == {"chapter_id", "action", "user_requirement"}
        assert set(params["required"]) == {"chapter_id", "action"}

    def test_description_declares_what_it_loads_itself(self):
        desc = self._tool().meta.description
        for word in ("自动加载", "本章要写什么", "上一章蓝图与结尾原文", "这一幕要干什么",
                     "主线骨架与终局", "人物想要什么", "还没收的伏笔", "全局设定"):
            assert word in desc, word
        assert "不必先读" in desc
        assert "写成了什么" not in desc, "「本章写成了什么」已删除（迁移 0022）"

    def test_user_requirement_forbids_restating_the_blueprint(self):
        desc = self._tool().meta.parameters["properties"]["user_requirement"]["description"]
        assert "用户" in desc
        assert "不要复述" in desc
        assert "自动加载" in desc

    def test_action_enum_is_write_continue_rewrite(self):
        props = self._tool().meta.parameters["properties"]
        assert props["action"]["enum"] == ["write", "continue", "rewrite"]


class TestTwoWritingRoutesAreDescribedSeparately:
    """两条写正文的路，对「要不要先读」的要求正好相反，工序必须分开说。

    把两条路合成一句话（「写正文不必自己先读」）说错过一次：那对
    ``call_writer_agent`` 成立，对 ``write_chapter`` / ``continue_chapter`` 是错的——
    后两个只把正文存库、不加载任何东西，不先读就等于盲写。
    """

    @staticmethod
    def _tool_usage() -> str:
        from app.agent.prompts.builder import get_prompt_builder

        return get_prompt_builder().get_static_section("tool_usage")

    def test_guide_splits_the_two_routes(self):
        text = self._tool_usage()
        assert "两条写正文的路" in text
        assert "`call_writer_agent`（写作智能体）：**不必先读**" in text
        assert "（你自己写正文）：**可以先读了解情况**" in text

    def test_subagent_route_explains_why_no_read(self):
        assert "它会自动加载" in self._tool_usage()

    def test_self_write_route_explains_why_it_must_read(self):
        usage = self._tool_usage()
        assert "这两个工具只把正文存库" in usage
        assert "不加载任何东西" in usage
        assert "read_chapter_package" in usage

    def test_analysis_still_reads_the_blueprint(self):
        """分析、规划、回答用户问题仍然要读蓝图——不能因为写正文的路不用读就一起删掉。"""
        assert "分析、规划、回答用户问题前先读章蓝图" in self._tool_usage()

    def test_write_chapter_warns_about_overwriting_and_names_the_read_tool(self):
        from app.agent.tools import get_tool_registry

        desc = get_tool_registry()["write_chapter"].meta.description
        assert "覆盖" in desc
        assert "read_chapter_content" in desc
        assert "不用再补任何摘要" in desc, "写完不用补「本章写成了什么」——下一章直接读结尾原文"

    def test_continue_chapter_warns_against_repeating_existing_prose(self):
        """content 是追加：传了全文会让正文翻倍。"""
        from app.agent.tools import get_tool_registry

        desc = get_tool_registry()["continue_chapter"].meta.description
        assert "追加" in desc
        assert "不重发已有正文" in desc
