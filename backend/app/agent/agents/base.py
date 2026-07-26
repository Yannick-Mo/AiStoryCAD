from __future__ import annotations

import json
import logging

from pydantic import BaseModel, ValidationError

from app.llm.client import LLMClient
from app.llm.types import Message
from app.agent.prompts import render_prompt
from app.agent.utils import parse_json

logger = logging.getLogger(__name__)


class BaseAgent:
    prompt_name: str = ""
    output_schema: type[BaseModel] = BaseModel

    async def _system_prompt(self, context: dict, user_prompt: str) -> str:
        persona_path = "persona"
        persona_data = render_prompt(persona_path)
        persona = persona_data if persona_data else ""

        available_skills = context.get("available_skills") or []
        if available_skills:
            skill_hints = [s.get("name", str(s)) for s in available_skills[:5]]
            persona = f"可用写作技能：{'、'.join(skill_hints)}\n" + persona

        kwargs = {"persona": persona, "user_prompt": user_prompt}
        kwargs.update(context)

        result = render_prompt(self.prompt_name, **kwargs)
        if not result:
            logger.error("Failed to render prompt '%s'", self.prompt_name)
            return persona

        rag_context = context.get("rag_context") or ""
        if rag_context:
            result += f"\n\n参考知识：\n{rag_context}"

        return result

    async def run(self, client: LLMClient, context: dict, user_prompt: str) -> BaseModel:
        system = await self._system_prompt(context, user_prompt)
        messages: list[Message] = [
            Message(role="system", content=system),
            Message(role="user", content="请按 JSON 格式输出"),
        ]
        result = await client.chat(messages)
        raw = result.content or ""
        try:
            parsed = parse_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("JSON parse failed on first attempt: %s. Retrying with strict JSON instruction.", e)
            messages.append(Message(role="assistant", content=raw))
            messages.append(Message(role="user", content="请输出合法 JSON 格式。不要包含任何 markdown 代码块标记，只输出纯 JSON。"))
            result = await client.chat(messages)
            raw = result.content or ""
            parsed = parse_json(raw)
        try:
            return self.output_schema.model_validate(parsed)
        except ValidationError as e:
            logger.error("Schema validation failed for agent output: %s", e)
            raise  # Re-raise instead of silently returning empty model