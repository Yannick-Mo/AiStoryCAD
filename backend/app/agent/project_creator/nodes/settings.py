from app.agent.project_creator.state import MaterialState
from app.agent.utils import get_shared_client, parse_json_safe, load_project_prompt, GenerationError
from app.llm.types import Message
from loguru import logger


async def build_settings(state: MaterialState) -> dict:
    client = get_shared_client()
    system_raw = load_project_prompt("material_settings")
    try:
        system = system_raw.format(
            genre=state.get("genre", ""),
            tone=state.get("tone", ""),
            plot_summary=state.get("plot_summary", ""),
            world_elements=state.get("world_elements", ""),
        )
    except KeyError as e:
        logger.error("build_settings: KeyError in system prompt template: {}", e)
        raise GenerationError(node_name="build_settings", message=f"Missing template key: {e}") from e

    messages: list[Message] = [
        Message(role="system", content=system),
        Message(role="user", content="请生成世界观设定"),
    ]

    result = await client.chat(messages, temperature=0.5, max_tokens=2048)
    raw = result.content or ""
    parsed = await parse_json_safe(raw, client, messages)

    return {"global_settings": parsed.get("global_settings", "")}
