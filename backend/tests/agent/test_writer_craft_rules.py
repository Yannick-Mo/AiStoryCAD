"""写手指令的创作侧约定 —— 这些是从真实产出里回捞出来的硬要求，不是措辞偏好。

本文件钉四类东西：

1. 提示词里必须留在场上的创作约束（篇幅下限、明喻密度、预告式旁白、冲突仲裁序、
   蓝图的分量对应正文的分量）；
2. 写手这次请求的生成参数：温度按动作分、读超时按"写满一章"给；
3. 工具层的超时必须比读超时更宽（否则正文写着写着被工具掐掉）；
4. 缺蓝图、缺下一章这类"材料不全"的情况有明确措辞，不留空行。

为什么值得钉：这几条都是从第 2 章的真实产出里回捞出来的——那一章章末出现了
「她不知道的是……」「他不知道的是……」这种站在人物之外替读者预告的旁白，
几乎每段挂了一个明喻，还把蓝图里"一句带过"的赵怀瑾扩写成了一整场戏。
"""
from __future__ import annotations

import pytest

from app.agent.agents.writer_agent import WritingAgent
from app.agent.prompts import PromptLoader, render_prompt


def _prompt(**overrides) -> str:
    ctx = dict(
        persona=PromptLoader().load("persona_writer").render(),
        project_title="T", chapter_title="归乡", chapter_number=2,
        act_name="第一幕", act_purpose="把他扣在镇上",
        chapter_goal="陈默回镇上查旧账，章末停在杂货铺门口。",
        length_target="4000–6700 字（本书已写 2 章，平均 5025 字）。写到 4000 字以下，"
                     "通常说明该停留的地方没写透；这一章的分量值得更长，就写更长。",
        next_chapter_title="第三章", next_chapter_goal="他决定留下。",
        genre="悬疑", logline="一个外人的丧事变成他的旧案",
        global_settings="小镇名叫渡口。",
        spine="奔丧 → 留下", ending="他留下查清旧债",
        previous_chapter_title="第一章", previous_chapter_goal="他拿到了账本。",
        previous_chapter_tail="他站在门口。",
        characters_text="- 陈默（主角）", open_setups_text="- 账本（第 1 章埋下）",
        action="write",
    )
    ctx.update(overrides)
    return render_prompt("writer", **ctx)


class TestCraftRulesStayInThePrompt:
    def test_length_reference_reaches_the_writer(self):
        prompt = _prompt()
        assert "篇幅参考" in prompt
        assert "4000–6700 字" in prompt
        assert "该停留的地方没写透" in prompt

    def test_simile_density_has_a_ceiling(self):
        prompt = _prompt()
        assert "明喻" in prompt
        assert "两三次" in prompt
        assert "同一个喻体" in prompt

    def test_omniscient_teaser_is_called_out(self):
        prompt = _prompt()
        assert "预告" in prompt
        assert "她不知道的是" in prompt, "第 2 章章末真的写成了这个句子"

    def test_conflict_arbitration_order_is_declared(self):
        prompt = _prompt()
        assert "上一章结尾原文说明的事实 > 本章蓝图 > 人物行为逻辑 > 类型惯例" in prompt

    def test_the_previous_chapters_blueprint_is_marked_as_merely_intended(self):
        """上一章的蓝图是计划，不是事实——正文可能没照它走，起点只认原文。"""
        prompt = _prompt()
        assert "它可能没完全照这份计划走" in prompt
        assert "蓝图是意图" in prompt

    def test_blueprint_weight_maps_to_chapter_weight(self):
        """蓝图里"一句带过"的事，正文不许扩成一场戏。"""
        prompt = _prompt()
        assert "一笔带过的事" in prompt
        assert "分量" in prompt

    def test_the_pre_write_questions_stay_internal(self):
        prompt = _prompt()
        assert "动笔之前" in prompt
        assert "不用写出来" in prompt

    def test_the_next_chapter_is_marked_as_off_limits(self):
        prompt = _prompt()
        assert "下一章要写什么" in prompt
        assert "这一章不要写它" in prompt

    def test_the_previous_chapter_is_not_to_be_replayed(self):
        """第 2 章开篇把第 1 章结尾的木匣、灵石、窗外脚步又演了一遍。"""
        prompt = _prompt()
        assert "不要再演一遍" in prompt
        assert "不要重来一遍" in prompt

    def test_rules_about_pacing_and_dialogue_are_present(self):
        prompt = _prompt()
        assert "对白连着走四五轮" in prompt

    def test_dialogue_quotes_are_pinned_to_one_style(self):
        """两个引号风格各写半章，是第 2 章接缝上真实留下的东西。"""
        prompt = _prompt()
        assert "对白一律用中文双引号" in prompt
        assert "「」" in prompt, "引文/信件这一类还要留一个口子"

    def test_hard_numbers_must_come_from_the_material(self):
        """第 1 章写「卡了六年」，第 2 章写「卡了七年」——数字是写手自己定的。"""
        prompt = _prompt()
        assert "硬数字只用手上材料里的" in prompt
        assert "年数" in prompt
        assert "价钱" in prompt

    def test_a_line_may_not_repeat_inside_one_chapter(self):
        prompt = _prompt()
        assert "一章里只出现一次" in prompt

    def test_rewrite_declares_its_authorization(self):
        prompt = _prompt(action="rewrite", existing_content="原文全文。")
        assert "重写的授权范围" in prompt
        assert "已经发生的事" in prompt

    def test_an_empty_blueprint_is_handled_not_left_blank(self):
        prompt = _prompt(chapter_goal="")
        assert "这一章还没有写蓝图" in prompt


class TestGenerationParameters:
    """写手这一次请求的参数：温度按动作分，读超时按"写满一章"给。"""

    @staticmethod
    def _stub():
        calls: list[dict] = []

        class StubClient:
            async def chat(self, messages, **kwargs):
                calls.append(kwargs)

                class _R:
                    content = "正文"
                    finish_reason = "stop"

                return _R()

        return StubClient(), calls

    async def _call(self, action: str | None = None) -> dict:
        client, calls = self._stub()
        ctx = {"chapter_title": "归乡", "chapter_goal": "查旧账", "project_title": "T",
               "chapter_number": 2, "genre": "悬疑"}
        if action is not None:
            ctx["action"] = action
        await WritingAgent().run(client, ctx, "请创作《归乡》的正文。")
        return calls[0]

    @pytest.mark.parametrize(("action", "expected"), [
        ("write", 0.85), ("continue", 0.8), ("rewrite", 0.7),
    ])
    async def test_temperature_follows_the_action(self, action: str, expected: float):
        assert (await self._call(action))["temperature"] == expected

    async def test_temperature_has_a_default(self):
        assert (await self._call())["temperature"] == 0.8

    async def test_the_writing_call_may_take_longer_than_the_default(self):
        call = await self._call("write")
        assert call["timeout"] == 600.0, "默认 120 秒的读超时会在长章上掐断生成"
        assert call["max_tokens"] == 16384

    def test_the_tool_timeout_is_wider_than_the_read_timeout(self):
        """工具超时比读超时窄的话，正文写着写着就被工具层掐掉。"""
        from app.agent.tools import get_tool_registry

        assert get_tool_registry()["call_writer_agent"].meta.timeout > 600
