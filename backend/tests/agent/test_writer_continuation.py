"""写手把一章写完的保证 —— 撞上长度上限要自己接着写，接缝上不许留下重复。

这一条来自 2026-09-13 的真实事故：第 2 章正文生成到 2023 字时撞上输出上限
（``finish_reason="length"``；实测原因是这个端点的模型把思考 token 记在
``max_tokens`` 里，一次请求 16384 个 token 里 14649 个花在思考上）。当时写手
把半章交回来，主 Agent 只好自己读回断点、再要一次续写，把两段手工接上——
接缝上留下了一句重复的描写（「院子不大。一口水缸，一棵老槐，」被重打了一遍）
和半章「」、半章 “ ” 的引号。

本文件钉住四件事：

1. 一次 ``run`` 之内自己把没写完的章补完，调用方不需要知道断点在哪；
2. 接缝去重（标点差一个句号也算重复）；
3. 补写轮数有上限，不会无限要下去；
4. 写作这条路默认只给一小份思考预算（low，可用环境变量改）——模型默认档位
   实测会烧掉上万 token，正文反而写不完；完全关掉能省一半时间，但产出也明显更薄。
"""
from __future__ import annotations

import pytest

from app.agent.agents.writer_agent import (
    WritingAgent,
    WritingResult,
    _continue_instruction,
    _drop_repeated_head,
    _join_parts,
    resolve_reasoning_effort,
)

BREAK_POINT = "院子不大。一口水缸，一棵老槐，"


class _StubClient:
    """按脚本返回 (content, finish_reason)，并记下每次调用的参数。"""

    def __init__(self, results: list[tuple[str, str]]):
        self.results = list(results)
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), **kwargs})
        content, finish = self.results.pop(0)

        class _R:
            pass

        r = _R()
        r.content = content
        r.finish_reason = finish
        return r


def _ctx(action: str = "write") -> dict:
    return {"chapter_title": "归乡", "chapter_goal": "查旧账", "project_title": "T",
            "chapter_number": 2, "genre": "悬疑", "action": action}


async def _run(results: list[tuple[str, str]]) -> tuple[_StubClient, WritingResult]:
    client = _StubClient(results)
    result = await WritingAgent().run(client, _ctx(), "请创作《归乡》的正文。")
    return client, result


class TestATruncatedChapterIsFinishedInOneRun:
    async def test_the_writer_asks_for_the_rest_itself(self):
        client, result = await _run([("前半章。" * 30, "length"), ("后半章。" * 30, "stop")])

        assert len(client.calls) == 2, "撞上上限后应当自己再要一次，而不是把半章交出去"
        assert result.truncated is False
        assert "前半章。" in result.text
        assert "后半章。" in result.text

    async def test_the_second_call_carries_the_break_point(self):
        client, _ = await _run([("前半章。" * 30, "length"), ("后半章。" * 30, "stop")])

        follow_up = client.calls[1]["messages"][-1]
        assert follow_up.role == "user"
        assert "前半章。" in follow_up.content, "要把已写末尾回给模型，否则它接不上"
        assert "接着往下写" in follow_up.content
        # 断点用的是用户消息：assistant 消息带思考内容时，重发会被接口拒绝
        assert all(m.role != "assistant" for m in client.calls[1]["messages"][1:])

    async def test_a_complete_chapter_costs_exactly_one_call(self):
        client, result = await _run([("整章写完。", "stop")])
        assert len(client.calls) == 1
        assert result.truncated is False
        assert result.text == "整章写完。"


class TestTheSeamKeepsNoRepeats:
    def test_the_break_sentence_is_not_written_twice(self):
        """真实接缝：上一段停在「一棵老槐，」，下一段把整句连同句号重打了一遍。"""
        stitched = _join_parts([BREAK_POINT,
                                f"{BREAK_POINT[:-1]}。槐树脚下垫着一方青石，磨得发亮。"])

        assert stitched.count("院子不大") == 1
        assert stitched.endswith("磨得发亮。")

    def test_plain_continuations_are_kept_with_a_blank_line(self):
        assert _join_parts(["第一段。", "第二段。"]) == "第一段。\n\n第二段。"

    def test_a_short_coincidental_overlap_is_left_alone(self):
        assert _drop_repeated_head("他笑了。", "他笑了很久。") == "他笑了很久。"

    def test_an_empty_continuation_does_not_leave_a_gap(self):
        assert _join_parts(["第一段。", "   ", "第二段。"]) == "第一段。\n\n第二段。"

    def test_the_instruction_marks_the_tail_as_material(self):
        """断点是外部文本：必须包在 <material> 里，并声明它只是定位用。"""
        instruction = _continue_instruction("他把匣盖扣上了。")
        assert "<material>" in instruction
        assert "</material>" in instruction
        assert "只用来定位断点" in instruction
        assert "不要重复" in instruction


class TestContinuationIsBounded:
    async def test_it_gives_up_after_the_round_limit(self):
        client, result = await _run([("还在写。" * 20, "length")] * 10)

        assert result.truncated is True, "补不满就要如实报上去，不能假装写完了"
        assert len(client.calls) == 4, "1 次首写 + 3 次补写"

    async def test_the_continuation_tail_is_capped(self):
        long_text = "字" * 5000
        client, _ = await _run([(long_text, "length"), ("收尾。", "stop")])
        assert "字" * 700 in client.calls[1]["messages"][-1].content
        assert "字" * 701 not in client.calls[1]["messages"][-1].content


class TestAnEmptyAnswerIsRetriedWithoutThinking:
    async def test_an_empty_answer_is_retried(self):
        client, result = await _run([("", "length"), ("正文。", "stop")])

        assert result.text == "正文。"
        assert len(client.calls) == 2

    async def test_the_retry_drops_thinking(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("WRITER_REASONING_EFFORT", "low")
        client, _ = await _run([("", "length"), ("正文。", "stop")])

        assert client.calls[0]["reasoning_effort"] == "low"
        assert client.calls[1]["reasoning_effort"] == "none", "预算被思考吃光时要换最省的一档"


class TestReasoningEffortDefaults:
    @pytest.mark.parametrize(("raw", "expected"), [
        (None, "low"),
        ("", "low"),
        ("none", "none"),
        ("off", "none"),
        ("low", "low"),
        ("medium", "medium"),
        ("inherit", None),
        ("default", None),
    ])
    def test_the_switch(self, monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: str | None):
        if raw is None:
            monkeypatch.delenv("WRITER_REASONING_EFFORT", raising=False)
        else:
            monkeypatch.setenv("WRITER_REASONING_EFFORT", raw)
        assert resolve_reasoning_effort() == expected

    async def test_writing_goes_out_with_a_small_thinking_budget(self, monkeypatch: pytest.MonkeyPatch):
        """默认不放开思考：模型默认档位会烧掉上万 token，正文反而写不完。"""
        monkeypatch.delenv("WRITER_REASONING_EFFORT", raising=False)
        client, _ = await _run([("整章写完。", "stop")])
        assert client.calls[0]["reasoning_effort"] == "low"

    async def test_thinking_can_be_turned_back_on(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("WRITER_REASONING_EFFORT", "low")
        client, _ = await _run([("整章写完。", "stop")])
        assert client.calls[0]["reasoning_effort"] == "low"