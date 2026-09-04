from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.llm.client import LLMClient
from app.llm.types import Message
from app.agent.prompts import render_prompt
from app.agent.utils import count_words

logger = logging.getLogger(__name__)


@dataclass
class WritingResult:
    """Output of a WritingAgent run.

    ``truncated`` is True when the provider stopped the generation at
    ``max_tokens`` (finish_reason == "length") — the caller must NOT treat
    the text as the complete scene body (and must not sync the blueprint
    against a possibly amputated ending).
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
        persona = render_prompt("persona") or ""

        kwargs = {
            "persona": persona,
            "user_prompt": user_prompt,
            **context,
        }

        system = render_prompt(self.prompt_name, **kwargs)
        if not system:
            logger.error("Failed to render writer prompt")
            return WritingResult()

        messages: list[Message] = [
            Message(role="system", content=system),
            Message(role="user", content="请直接输出正文，不要添加任何解释。"),
        ]

        text, truncated = await self._generate(client, messages)
        if not text:
            logger.warning("WritingAgent returned empty content, retrying")
            messages.append(Message(role="assistant", content="（无输出）"))
            messages.append(Message(role="user", content="请输出正文内容。"))
            text, truncated = await self._generate(client, messages)

        if text:
            wc = count_words(text)
            logger.info("WritingAgent generated %d characters (truncated=%s)", wc, truncated)
            return WritingResult(text=text, truncated=truncated, characters=wc)

        logger.error("WritingAgent failed to generate content after retry")
        return WritingResult()

    async def _generate(
        self, client: LLMClient, messages: list[Message]
    ) -> tuple[str, bool]:
        """One generation attempt. Returns (text, truncated)."""
        result = await client.chat(messages, temperature=0.8, max_tokens=16384)
        text = (result.content or "").strip()
        truncated = getattr(result, "finish_reason", "") == "length"
        return text, truncated
