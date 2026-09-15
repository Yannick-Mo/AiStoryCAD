"""设计人物与角色关系。

人物要回答三件事：他是谁（静态）、他想要什么（当下）、他从哪到哪（弧线）——
所以除了 personality / appearance / background / motivation，还有 arc。
角色关系在状态里用 from_name / to_name 指人（写库时才换成角色行 id）。
"""
from loguru import logger

from app.agent.project_creator.state import MaterialState, as_text, normalize_role
from app.agent.utils import get_shared_client, parse_json_safe, load_project_prompt, GenerationError
from app.llm.types import Message

DEFAULT_REL_TYPE = "关联"


def _raw_chars_text(raw_chars: list[dict]) -> str:
    items = []
    for c in raw_chars or []:
        if not isinstance(c, dict):
            continue
        name = as_text(c.get("name"))
        if name:
            items.append(f"- {name}: {as_text(c.get('description'))}")
    return "\n".join(items) if items else "素材中未明确提及角色"


def _first_name(item: dict, *keys: str) -> str:
    """按优先级取第一个非空的名字字段（兼容模型偶尔写回的旧字段名）。"""
    for key in keys:
        value = as_text(item.get(key))
        if value:
            return value
    return ""


async def design_characters(state: MaterialState) -> dict:
    client = get_shared_client()
    system_raw = load_project_prompt("material_characters")
    try:
        system = system_raw.format(
            genre=state.get("genre", ""),
            tone=state.get("tone", ""),
            plot_summary=state.get("plot_summary", ""),
            ending=state.get("ending", ""),
            characters_raw_text=_raw_chars_text(state.get("characters_raw", [])),
        )
    except KeyError as e:
        logger.error("design_characters: KeyError in system prompt template: {}", e)
        raise GenerationError(node_name="design_characters", message=f"Missing template key: {e}") from e

    messages: list[Message] = [
        Message(role="system", content=system),
        Message(role="user", content="请设计人物与角色关系"),
    ]

    result = await client.chat(messages, temperature=0.5, max_tokens=8192)
    raw = result.content or ""
    parsed = await parse_json_safe(raw, client, messages, node_name="design_characters")

    raw_characters = parsed.get("characters")
    if not isinstance(raw_characters, list):
        raw_characters = []

    characters: list[dict] = []
    for item in raw_characters:
        c = item if isinstance(item, dict) else {}
        characters.append({
            "name": as_text(c.get("name")),
            "role": normalize_role(c.get("role")),
            "personality": as_text(c.get("personality")),
            "appearance": as_text(c.get("appearance")),
            "background": as_text(c.get("background")),
            "motivation": as_text(c.get("motivation")),
            "arc": as_text(c.get("arc")),
        })

    raw_relations = parsed.get("relations")
    if not isinstance(raw_relations, list):
        raw_relations = []

    relations: list[dict] = []
    for item in raw_relations:
        r = item if isinstance(item, dict) else {}
        relations.append({
            "from_name": _first_name(r, "from_name", "char_name", "source_name"),
            "to_name": _first_name(r, "to_name", "target_name"),
            "rel_type": as_text(r.get("rel_type")) or DEFAULT_REL_TYPE,
            "label": as_text(r.get("label")),
            "description": as_text(r.get("description")),
        })

    return {"characters": characters, "relations": relations}
