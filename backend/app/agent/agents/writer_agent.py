from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.llm.client import LLMClient
from app.llm.types import Message
from app.agent.prompts import render_prompt
from app.agent.utils import count_words

logger = logging.getLogger(__name__)

# 一次写作要写满一章。默认 120 秒的读超时在长章上会中途掐断生成（接着重试，
# 然后本章留下一段没写完的正文），所以这一次请求单独给足。
_WRITE_READ_TIMEOUT_S = 600.0

_WRITE_MAX_TOKENS = 16384

# 按动作给温度：新写要一点野性；重写是贴着原文改，收一点；续写取中间。
_TEMPERATURE = {"write": 0.85, "continue": 0.8, "rewrite": 0.7}
_DEFAULT_TEMPERATURE = 0.8

# 一章没写完就接着要：最多补几轮、每轮回给模型多少字的断点上文。
_MAX_CONTINUATIONS = 3
_CONTINUE_TAIL_CHARS = 700

# 思考档位——这是实测出来的事故结论：这个端点的 deepseek-v4-flash 是思考模型，
# 而**思考 token 记在 max_tokens 里**。实测 max_tokens=16384 的一次请求里 14649 个
# token 花在思考上，正文写出 2000 来字就撞上限（finish_reason="length"）；还有一次
# 整个预算被思考吃光，正文返回空串。第 2 章正文就是这么被截断的：写手交回 2023 字，
# 上层只好自己续，接缝上留下了一句重复的描写和半章「」、半章 “ ” 的引号。
# 默认档位取 low（不是 none）：同一章蓝图、同一套提示词跑过一次 A/B——
#   low  ：4066 字，38 秒，不截断。场景更厚，豆彪与主角的腔调都在。
#   none ：2739 字，18 秒，不截断。干净，但明显更薄、比本书已写章短一截。
# 也就是说思考省下来的是时间，不是白拿的质量——所以默认给一小份思考预算，
# 需要极致速度时用 WRITER_REASONING_EFFORT=none 覆盖。
_REASONING_EFFORT_ENV = "WRITER_REASONING_EFFORT"
_DEFAULT_REASONING_EFFORT = "low"
_NO_REASONING_ALIASES = {"none", "off", "false", "0", "no", "disabled"}
_INHERIT_ALIASES = {"inherit", "default", "model", "auto"}

# 比对断点时只认字，不认标点：模型的接缝常常是同一句、只差一个句号或逗号。
_PUNCT = "。！？，、；：…—·「」『』“”‘’（）《》〈〉?!,.;:\"'()[]"
_PUNCT_TABLE = {ord(ch): "" for ch in _PUNCT}


def resolve_reasoning_effort() -> str | None:
    """写作这一次请求的思考档位：默认关掉，可用 WRITER_REASONING_EFFORT 覆盖。"""
    raw = (os.getenv(_REASONING_EFFORT_ENV) or "").strip().lower()
    if raw in _INHERIT_ALIASES:
        return None
    if not raw:
        return _DEFAULT_REASONING_EFFORT
    if raw in _NO_REASONING_ALIASES:
        return "none"
    return raw


def _skeleton(text: str) -> str:
    """只留字：标点与空白都不算。"""
    return text.translate(_PUNCT_TABLE).strip()


def _drop_repeated_head(prev: str, nxt: str, limit: int = 400) -> str:
    """去掉接续段落开头与已写结尾重复的那一段（模型常把断点那句重打一遍）。"""
    max_size = min(limit, len(prev), len(nxt))
    for size in range(max_size, 7, -1):
        head = _skeleton(nxt[:size])
        if head and head == _skeleton(prev[-size:]):
            return nxt[size:].lstrip()
    return nxt


def _join_parts(parts: list[str]) -> str:
    """把几轮写出来的正文接成一章，接缝处不留下重复。"""
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not out:
            out = part
            continue
        trimmed = _drop_repeated_head(out, part)
        if trimmed == part:
            out = f"{out}\n\n{part}"
        else:
            # 断点被重打了一遍：直接接上，不再空行（上一段可能正停在逗号上）。
            out = f"{out}{trimmed}"
    return out.strip()


def _continue_instruction(tail: str) -> str:
    return (
        "（上面这一段是你刚写出的正文的结尾，只用来定位断点，不要重复它。）\n"
        f"<material>\n{tail}\n</material>\n"
        "接着往下写：从断点处继续，不要重复已经写过的句子，不要重写开头，"
        "也不要急着收尾——该演完的场面照常演完。直接输出后续正文。"
    )


@dataclass
class WritingResult:
    """Output of a WritingAgent run.

    ``truncated`` is True when the provider stopped the generation at
    ``max_tokens`` (finish_reason == "length") — the caller must NOT treat
    the text as the complete scene body, since the ending may be amputated.
    """

    text: str = ""
    truncated: bool = False
    characters: int = 0


class WritingAgent:
    """专业写作智能体 — 专注正文创作，无工具、无安全规则干扰。
    - 不输出 JSON，输出纯正文文本
    - 使用更高 temperature (0.8) 以获得创造性
    - prompt 中不包含工具定义、安全规则、模式限制
    """

    prompt_name = "writer"

    async def run(
        self,
        client: LLMClient,
        context: dict,
        user_prompt: str,
    ) -> WritingResult:
        # 写作链路用专用人格：persona.yaml 是编辑型人格，里面的「不替用户直接写
        # 内容 / 先解释推理 / 反问澄清」和「直接输出正文」的任务直接冲突。
        persona = render_prompt("persona_writer") or ""

        kwargs = {
            "persona": persona,
            "user_prompt": user_prompt,
            **context,
        }

        system = render_prompt(self.prompt_name, **kwargs)
        if not system:
            logger.error("Failed to render writer prompt")
            return WritingResult()

        ask = user_prompt.strip() if user_prompt else "请写出这一章的正文。"
        # 用户消息是模型最后读到的一段：把「这是在写小说正文」放在这里，比埋在
        # 系统提示中部更贴近生成时的注意力。
        messages: list[Message] = [
            Message(role="system", content=system),
            Message(
                role="user",
                content=(
                    f"{ask}\n"
                    "你写的是小说正文：先让它好读，再谈其余约束。\n"
                    "直接输出正文，不要解释、不要标题、不要 markdown。"
                ),
            ),
        ]

        temperature = _TEMPERATURE.get(str(context.get("action") or ""), _DEFAULT_TEMPERATURE)
        text, truncated = await self._write(client, messages, temperature)

        if not text:
            logger.error("WritingAgent failed to generate content")
            return WritingResult()

        wc = count_words(text)
        logger.info(
            "WritingAgent generated %d characters (truncated=%s, reasoning_effort=%s)",
            wc, truncated, resolve_reasoning_effort(),
        )
        return WritingResult(text=text, truncated=truncated, characters=wc)

    async def _write(
        self, client: LLMClient, messages: list[Message], temperature: float
    ) -> tuple[str, bool]:
        """写到写完为止：撞上长度上限就接着写，最多补 _MAX_CONTINUATIONS 轮。"""
        effort = resolve_reasoning_effort()
        parts: list[str] = []
        truncated = False
        empty_retried = False
        continuations = 0
        while True:
            text, truncated = await self._generate(client, messages, temperature, effort)
            if not text:
                if parts or empty_retried:
                    break
                # 空输出的那次是预算被思考吃光了（实测见过一次）：换最省的一档再要一次。
                empty_retried = True
                effort = "none"
                messages = [
                    *messages,
                    Message(role="assistant", content="（无输出）"),
                    Message(role="user", content="请输出正文内容。"),
                ]
                continue
            parts.append(text)
            if not truncated or continuations >= _MAX_CONTINUATIONS:
                break
            continuations += 1
            messages = [
                *messages,
                Message(role="user", content=_continue_instruction(text[-_CONTINUE_TAIL_CHARS:])),
            ]
        return _join_parts(parts), truncated

    async def _generate(
        self,
        client: LLMClient,
        messages: list[Message],
        temperature: float,
        reasoning_effort: str | None = None,
    ) -> tuple[str, bool]:
        """One generation attempt. Returns (text, truncated)."""
        result = await client.chat(
            messages,
            temperature=temperature,
            max_tokens=_WRITE_MAX_TOKENS,
            timeout=_WRITE_READ_TIMEOUT_S,
            reasoning_effort=reasoning_effort,
        )
        text = (result.content or "").strip()
        truncated = getattr(result, "finish_reason", "") == "length"
        return text, truncated