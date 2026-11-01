"""定故事开头这一段的走向：一句话梗概 / 当前已完成骨架。

这里只定**开头这一段**要走到哪，不预写全书结局——后面的走向等续写时再往后追加。
骨架先于幕章结构定下来，后面的结构规划和逐章蓝图才有"这一段该往哪使劲"的依据。
"""
from loguru import logger

from app.agent.project_creator.state import MaterialState, as_text
from app.agent.utils import get_shared_client, parse_json_safe, load_project_prompt, GenerationError
from app.llm.types import Message


async def plan_spine(state: MaterialState) -> dict:
    client = get_shared_client()
    system_raw = load_project_prompt("material_spine")
    try:
        system = system_raw.format(
            genre=state.get("genre", ""),
            tone=state.get("tone", ""),
            plot_summary=state.get("plot_summary", ""),
            world_elements=state.get("world_elements", ""),
            global_settings=state.get("global_settings", ""),
        )
    except KeyError as e:
        logger.error("plan_spine: KeyError in system prompt template: {}", e)
        raise GenerationError(node_name="plan_spine", message=f"Missing template key: {e}") from e

    messages: list[Message] = [
        Message(role="system", content=system),
        Message(role="user", content="请定下故事开头这一段的梗概与当前已完成骨架"),
    ]

    result = await client.chat(messages, temperature=0.5, max_tokens=2048)
    raw = result.content or ""
    parsed = await parse_json_safe(raw, client, messages, node_name="plan_spine")

    return {
        "logline": as_text(parsed.get("logline")),
        "spine": as_text(parsed.get("spine")),
    }
