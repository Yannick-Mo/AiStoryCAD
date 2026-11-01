"""逐幕写章蓝图：一章一段话，说清这一章要写什么。

这个位置原来是「场景规划」节点——它给每一章拆 3-4 个场景。场景层删除后，同样的位置
改为写**章蓝图**（chapters.goal），并且只写一层：章蓝图说这一章整体从哪走到哪，
不再往下拆场（规格第 6 节）。

为什么按幕并发、而不是整本一次调用：一本 15-30 章的蓝图是 15-30 段话，
一次生成必然撞上输出上限而被截断；一次一幕（4-8 章）每段才写得完整。
蓝图怎么写由 blueprint.py 的 render_guide("chapter") 统一提供，模板里只留占位符。
"""
import asyncio

from loguru import logger

from app.agent.blueprint import render_guide
from app.agent.project_creator.state import MaterialState, as_int, as_text
from app.agent.utils import get_shared_client, parse_json_safe, load_project_prompt, GenerationError
from app.llm.types import Message

MAX_CONCURRENCY = 4


def _chapters_text(chapters: list[dict]) -> str:
    return "\n".join(f"{i}. 《{as_text(ch.get('title')) or '未命名'}》" for i, ch in enumerate(chapters, start=1))


def _characters_text(characters: list[dict]) -> str:
    if not characters:
        return "（还没有设计人物，先按章名与当前已完成骨架写）"
    lines = []
    for c in characters:
        if not isinstance(c, dict):
            continue
        name = as_text(c.get("name"))
        if not name:
            continue
        lines.append(f"- {name}（{as_text(c.get('role'))}）：{as_text(c.get('motivation'))}")
    return "\n".join(lines) if lines else "（还没有设计人物，先按章名与当前已完成骨架写）"


async def _blueprint_one_act(
    act_index: int,
    act: dict,
    act_chapters: list[dict],
    characters: list[dict],
    spine: str,
) -> list[str]:
    """跑一次模型，返回这一幕每一章的蓝图（按章序，可能比章数少）。"""
    client = get_shared_client()
    system_raw = load_project_prompt("material_chapters")
    try:
        system = system_raw.format(
            act_name=as_text(act.get("name")) or f"第{act_index + 1}幕",
            act_purpose=as_text(act.get("purpose")),
            chapters_text=_chapters_text(act_chapters),
            characters_text=_characters_text(characters),
            spine=spine,
            blueprint_spec=render_guide("chapter"),
        )
    except KeyError as e:
        logger.error("_blueprint_one_act: KeyError in chapters prompt template: {}", e)
        raise GenerationError(node_name="write_chapter_blueprints", message=f"Missing template key: {e}") from e

    messages: list[Message] = [
        Message(role="system", content=system),
        Message(role="user", content="请为这一幕的每一章写一段章蓝图"),
    ]

    result = await client.chat(messages, temperature=0.6, max_tokens=8192)
    raw = result.content or ""
    parsed = await parse_json_safe(raw, client, messages, node_name="write_chapter_blueprints")

    raw_chapters = parsed.get("chapters")
    if not isinstance(raw_chapters, list):
        return []
    goals: list[str] = []
    for item in raw_chapters:
        goals.append(as_text(item.get("goal")) if isinstance(item, dict) else "")
    return goals


async def write_chapter_blueprints(state: MaterialState) -> dict:
    acts: list = list(state.get("acts") or [])
    chapters: list[dict] = [dict(ch) for ch in (state.get("chapters") or []) if isinstance(ch, dict)]
    if not acts or not chapters:
        return {"chapters": chapters}

    # 按幕分组，保持章在原列表里的顺序
    groups: dict[int, list[int]] = {}
    for position, chapter in enumerate(chapters):
        act_index = as_int(chapter.get("act_index"), -1)
        groups.setdefault(act_index, []).append(position)

    targets = [
        (act_index, positions)
        for act_index, positions in groups.items()
        # 越界的幕不猜也不改：writer 会把这些章如实报进 truncated
        if 0 <= act_index < len(acts)
    ]

    characters = [c for c in (state.get("characters") or []) if isinstance(c, dict)]
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _run(act_index: int, positions: list[int]) -> list[str]:
        async with semaphore:
            return await _blueprint_one_act(
                act_index,
                acts[act_index] if isinstance(acts[act_index], dict) else {},
                [chapters[p] for p in positions],
                characters,
                as_text(state.get("spine")),
            )

    results = await asyncio.gather(*(_run(act_index, positions) for act_index, positions in targets))

    errors: list[str] = []
    # strict=True：results 是 asyncio.gather(*targets) 的返回值，长度天然等于 targets。
    # 真出现不等长就说明并发调度出了问题——宁可当场炸掉，也不要静默漏掉几幕的章蓝图。
    for (act_index, positions), goals in zip(targets, results, strict=True):
        for offset, position in enumerate(positions):
            goal = goals[offset] if offset < len(goals) else ""
            if goal:
                chapters[position]["goal"] = goal
            else:
                title = as_text(chapters[position].get("title")) or "未命名"
                errors.append(f"第{act_index + 1}幕 第{offset + 1}章《{title}》没有拿到章蓝图")

    if not errors:
        return {"chapters": chapters}
    return {"chapters": chapters, "errors": list(state.get("errors") or []) + errors}
