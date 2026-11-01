"""规划开头的幕与章：幕名 + 这一幕承担什么转变，以及每一幕有哪些章。

只规划故事开头这一段，不预写全书结构；这一步只定**结构**（幕、章名、章属于哪一幕），
不写每一章的蓝图——蓝图由 blueprints 节点逐幕写成一段话。章在状态里是扁平列表，
靠 act_index（0-based）指回幕；这样 writer 只需要一处越界判断。
"""
from loguru import logger

from app.agent.project_creator.state import MaterialState, as_text, as_int
from app.agent.utils import get_shared_client, parse_json_safe, load_project_prompt, GenerationError
from app.llm.types import Message

# 模型没给 estimated_words 时用全书默认目标；**不能拿开头几章的字数当全书目标**。
DEFAULT_TOTAL_WORDS = 100000


async def plan_structure(state: MaterialState) -> dict:
    client = get_shared_client()
    system_raw = load_project_prompt("material_structure")
    try:
        system = system_raw.format(
            genre=state.get("genre", ""),
            tone=state.get("tone", ""),
            logline=state.get("logline", ""),
            plot_summary=state.get("plot_summary", ""),
            world_elements=state.get("world_elements", ""),
            spine=state.get("spine", ""),
        )
    except KeyError as e:
        logger.error("plan_structure: KeyError in system prompt template: {}", e)
        raise GenerationError(node_name="plan_structure", message=f"Missing template key: {e}") from e

    messages: list[Message] = [
        Message(role="system", content=system),
        Message(role="user", content="请规划开头这一段的幕-章结构（只规划开头，不规划全书）"),
    ]

    result = await client.chat(messages, temperature=0.5, max_tokens=8192)
    raw = result.content or ""
    parsed = await parse_json_safe(raw, client, messages, node_name="plan_structure")

    acts: list[dict] = []
    chapters: list[dict] = []

    # 模型给的是嵌套结构（幕里带章），这里摊平成 acts + chapters，
    # 并把幕的位置写成 act_index。非字典的条目也照样占一个位置，
    # 否则后面的幕的 act_index 会整体前移、和模型的意图对不上。
    raw_acts = parsed.get("acts")
    if not isinstance(raw_acts, list):
        raw_acts = []

    for act_idx, raw_act in enumerate(raw_acts):
        act = raw_act if isinstance(raw_act, dict) else {}
        acts.append({
            "name": as_text(act.get("name")),
            "purpose": as_text(act.get("purpose")),
        })
        raw_chapters = act.get("chapters")
        if not isinstance(raw_chapters, list):
            continue
        for raw_chapter in raw_chapters:
            chapter = raw_chapter if isinstance(raw_chapter, dict) else {}
            chapters.append({
                "title": as_text(chapter.get("title")),
                "goal": "",  # 蓝图由 write_chapter_blueprints 逐幕补
                "act_index": act_idx,
            })

    estimated = as_int(parsed.get("estimated_words"), 0)
    if estimated <= 0:
        # 开头几章的字数不是全书目标：模型没给就用默认长篇目标。
        estimated = DEFAULT_TOTAL_WORDS

    return {"acts": acts, "chapters": chapters, "estimated_words": estimated}
