from app.agent.project_creator.state import MaterialState
from app.agent.utils import get_shared_client, parse_json_safe, load_project_prompt, GenerationError
from app.llm.types import Message
from loguru import logger


def _raw_chars_text(raw_chars: list[dict]) -> str:
    if not raw_chars:
        return "素材中未明确提及角色"
    return "\n".join(f"- {c['name']}: {c.get('description', '')}" for c in raw_chars)


async def design_characters(state: MaterialState) -> dict:
    client = get_shared_client()
    system_raw = load_project_prompt("material_characters")
    try:
        system = system_raw.format(
            genre=state.get("genre", ""),
            tone=state.get("tone", ""),
            plot_summary=state.get("plot_summary", ""),
            characters_raw_text=_raw_chars_text(state.get("characters_raw", [])),
        )
    except KeyError as e:
        logger.error("design_characters: KeyError in system prompt template: {}", e)
        raise GenerationError(node_name="design_characters", message=f"Missing template key: {e}") from e

    messages: list[Message] = [
        Message(role="system", content=system),
        Message(role="user", content="请设计角色"),
    ]

    result = await client.chat(messages, temperature=0.5, max_tokens=4096)
    raw = result.content or ""
    parsed = await parse_json_safe(raw, client, messages, node_name="design_characters")

    characters = parsed.get("characters", [])
    for c in characters:
        c.setdefault("role", "supporting")
        c.setdefault("personality", "")
        c.setdefault("appearance", "")
        c.setdefault("background", "")
        c.setdefault("motivation", "")

    relations = parsed.get("relations", [])
    for r in relations:
        r.setdefault("rel_type", "关联")
        r.setdefault("label", "")
        r.setdefault("description", "")

    return {"characters": characters, "relations": relations}
