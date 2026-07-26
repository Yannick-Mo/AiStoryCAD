import asyncio
from app.agent.project_creator.state import MaterialState, SceneDef
from app.agent.utils import get_shared_client, parse_json_safe, load_project_prompt, GenerationError
from app.llm.types import Message
from loguru import logger


def _chars_text(raw_chars: list[dict], designed_chars: list[dict]) -> str:
    parts = []
    if raw_chars:
        parts.append("素材中提及的角色：")
        for c in raw_chars:
            parts.append(f"- {c.get('name', '')}: {c.get('description', '')}")
    if designed_chars:
        parts.append("已设计的角色：")
        for c in designed_chars:
            info = f"- {c.get('name', '')} ({c.get('role', '')}): {c.get('personality', '')}"
            parts.append(info)
    return "\n".join(parts) if parts else "暂无角色"


async def _generate_one_chapter(
    act_idx: int,
    chap_idx: int,
    act_name: str,
    chapter_title: str,
    chapter_goal: str,
    characters_raw: list[dict],
    designed_chars: list[dict],
    world_elements: str,
) -> list[SceneDef]:
    client = get_shared_client()
    system_raw = load_project_prompt("material_scenes")
    try:
        system = system_raw.format(
            act_name=act_name,
            chapter_title=chapter_title,
            chapter_goal=chapter_goal,
            characters_raw_text=_chars_text(characters_raw, designed_chars),
            world_elements=world_elements,
        )
    except KeyError as e:
        logger.error("_generate_one_chapter: KeyError in scenes prompt template: {}", e)
        raise GenerationError(node_name="scenes", message=f"Missing template key: {e}") from e
    messages: list[Message] = [
        Message(role="system", content=system),
        Message(role="user", content="请为这一章规划场景"),
    ]
    result = await client.chat(messages, temperature=0.6, max_tokens=2048)
    raw = result.content or ""
    parsed = await parse_json_safe(raw, client, messages, node_name="scenes")
    scene_dicts = parsed.get("scenes", [])
    scenes: list[SceneDef] = []
    for sc in scene_dicts:
        scenes.append(SceneDef(
            act_idx=act_idx,
            chapter_idx=chap_idx,
            title=sc.get("title", ""),
            pov_character=sc.get("pov_character", ""),
            setting=sc.get("setting", ""),
            scene_time=sc.get("scene_time", ""),
            summary=sc.get("summary", ""),
        ))
    return scenes


async def generate_all_scenes(state: MaterialState) -> dict:
    sem = asyncio.Semaphore(5)

    async def _generate_one(
        act_idx: int,
        chap_idx: int,
        act_name: str,
        chapter_title: str,
        chapter_goal: str,
        characters_raw: list[dict],
        designed_chars: list[dict],
        world_elements: str,
    ) -> list[SceneDef]:
        async with sem:
            return await _generate_one_chapter(
                act_idx, chap_idx,
                act_name,
                chapter_title,
                chapter_goal,
                characters_raw,
                designed_chars,
                world_elements,
            )

    tasks = []
    for act_idx, act in enumerate(state.get("acts", [])):
        for chap_idx, chapter in enumerate(act.get("chapters", [])):
            tasks.append(_generate_one(
                act_idx, chap_idx,
                act.get("name", ""),
                chapter.get("title", ""),
                chapter.get("goal", ""),
                state.get("characters_raw", []),
                state.get("characters", []),
                state.get("world_elements", ""),
            ))

    results = await asyncio.gather(*tasks)
    all_scenes: list[SceneDef] = []
    for scenes in results:
        all_scenes.extend(scenes)
    return {"scenes": all_scenes}
