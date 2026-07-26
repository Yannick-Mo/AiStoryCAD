import json
import logging
from app.llm.client import get_shared_client
from app.llm.types import Message

logger = logging.getLogger(__name__)


STARTER_SYSTEM_PROMPT = """你是一个创意故事生成器。根据用户指定的类型和风格，生成一个新颖的故事情节起点。
返回 JSON 格式，包含以下字段：
- title: 故事标题（中文，有吸引力）
- hook: 一句话钩子（吸引读者，30字以内）
- premise: 核心设定（2-3句话）
- protagonist: 主角原型描述（包括身份、性格特点）
- opening_scene: 开篇场景（100-200字，中文）
- themes: 可能主题（数组，每项10字以内）
- tags: 标签（数组，每项5字以内）

要求：
1. 每个生成结果必须完全不同，避免重复套路
2. 充分运用该类型的经典元素和叙事模式
3. 开篇场景要有画面感和代入感
4. 保持原创性，不要套用知名作品的设定"""

BATCH_SYSTEM_PROMPT = """你是一位创意写作助手，为指定类型生成多个故事开头。
请返回以下 JSON 格式（不要返回数组，要返回对象）：
{
  "results": [
    {
      "title": "故事标题",
      "premise": "一句话前提",
      "opening_scene": "开场场景描述",
      "hook": "吸引读者的悬念"
    }
  ]
}"""

CHALLENGE_SYSTEM_PROMPT = """你是一个创作挑战生成器。生成带有创意限制条件的写作挑战。
返回 JSON 格式，包含以下字段：
- title: 挑战标题（中文，3-8字）
- description: 挑战描述（2-3句话，设定情境）
- constraints: 约束条件列表（每项一个明确的写作限制）
- genre: 类型
- difficulty: 难度（easy/medium/hard）

难度定义：
- easy: 1个约束条件
- medium: 2-3个约束条件
- hard: 4个及以上约束条件

要求：
1. 挑战要有趣且有可写性
2. 约束条件要具体、可操作
3. 避免过于抽象或无法验证的约束"""


class InspirationGenerator:

    def __init__(self):
        self._client = get_shared_client().fork()

    async def generate_story_starter(
        self,
        genre: str,
        style: str = "",
        constraints: list[str] | None = None,
    ) -> dict | None:
        user_content = f"类型：{genre}"
        if style:
            user_content += f"\n风格：{style}"
        if constraints:
            user_content += f"\n额外约束：{'；'.join(constraints)}"
        result = await self._client.chat(
            messages=[
                Message(role="system", content=STARTER_SYSTEM_PROMPT),
                Message(role="user", content=user_content),
            ],
            response_format="json_object",
        )
        try:
            return json.loads(result.content)
        except json.JSONDecodeError:
            pass
        import re
        json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', result.content)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        try:
            start = result.content.find('{')
            end = result.content.rfind('}')
            if start != -1 and end > start:
                return json.loads(result.content[start:end + 1])
        except json.JSONDecodeError:
            pass
        return None

    async def generate_challenge(
        self,
        difficulty: str = "medium",
        genre: str = "",
    ) -> dict:
        user_content = f"难度：{difficulty}"
        if genre:
            user_content += f"\n类型：{genre}"
        result = await self._client.chat(
            messages=[
                Message(role="system", content=CHALLENGE_SYSTEM_PROMPT),
                Message(role="user", content=user_content),
            ],
            response_format="json_object",
        )
        try:
            return json.loads(result.content)
        except json.JSONDecodeError:
            return {}

    async def batch_generate(
        self,
        genres: list[str],
        count: int = 3,
    ) -> list[dict]:
        results = []
        for genre in genres:
            try:
                user_content = f"类型：{genre}\n生成 {count} 个不同的起点，确保不重复"
                result = await self._client.chat(
                    messages=[
                        Message(role="system", content=BATCH_SYSTEM_PROMPT),
                        Message(role="user", content=user_content),
                    ],
                    response_format="json_object",
                    max_tokens=4096,
                )
                data = json.loads(result.content)
                if isinstance(data, dict) and "results" in data:
                    results.extend(data["results"])
                elif isinstance(data, list):
                    results.extend(data)
            except (json.JSONDecodeError, KeyError, Exception) as exc:
                logger.warning("batch_generate skipped genre=%s: %s", genre, exc)
                continue
        return results
