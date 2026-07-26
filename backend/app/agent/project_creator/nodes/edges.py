from app.agent.project_creator.state import MaterialState, EdgeDef
from app.agent.utils import get_shared_client, parse_json_safe, load_project_prompt
from app.llm.types import Message


def _build_chapter_index(acts: list[dict]) -> list[dict]:
    """Flatten chapters with their act/chapter index for edge generation."""
    flat = []
    for act_idx, act in enumerate(acts):
        for chap_idx, ch in enumerate(act.get("chapters", [])):
            flat.append({
                "act_idx": act_idx,
                "chapter_idx": chap_idx,
                "title": ch.get("title", ""),
                "goal": ch.get("goal", ""),
            })
    return flat


def _build_timeline_edges(flat_chapters: list[dict]) -> list[EdgeDef]:
    """Programmatically generate timeline edges connecting chapters in order."""
    edges: list[EdgeDef] = []
    for i in range(len(flat_chapters) - 1):
        src = flat_chapters[i]
        tgt = flat_chapters[i + 1]
        edges.append(EdgeDef(
            source_act_idx=src["act_idx"],
            source_chapter_idx=src["chapter_idx"],
            target_act_idx=tgt["act_idx"],
            target_chapter_idx=tgt["chapter_idx"],
            type="timeline",
            label="",
        ))
    return edges


async def generate_edges(state: MaterialState) -> dict:
    acts = state.get("acts", [])
    if not acts:
        return {"edges": []}

    flat = _build_chapter_index(acts)
    timeline_edges = _build_timeline_edges(flat)

    if len(flat) < 2:
        return {"edges": timeline_edges}

    client = get_shared_client()
    system_prompt = load_project_prompt("material_edges")

    chapter_text = "\n".join(
        f"第{c['act_idx']+1}幕 第{c['chapter_idx']+1}章：《{c['title']}》——{c['goal']}"
        for c in flat
    )

    chars_text = ""
    chars = state.get("characters", [])
    if chars:
        chars_text = "\n".join(f"- {c.get('name','')}({c.get('role','')}): {c.get('personality','')}" for c in chars)

    messages: list[Message] = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=f"章节列表：\n{chapter_text}\n\n角色列表：\n{chars_text}\n\n情节概要：{state.get('plot_summary', '')}"),
    ]

    result = await client.chat(messages, temperature=0.5, max_tokens=4096)
    raw = result.content or ""
    parsed = await parse_json_safe(raw, client, messages, node_name="generate_edges")
    narrative_edges_raw = parsed.get("edges", [])

    narrative_edges: list[EdgeDef] = []
    for e in narrative_edges_raw:
        narrative_edges.append(EdgeDef(
            source_act_idx=e.get("source_act_idx", 0),
            source_chapter_idx=e.get("source_chapter_idx", 0),
            target_act_idx=e.get("target_act_idx", 0),
            target_chapter_idx=e.get("target_chapter_idx", 0),
            type=e.get("type", "causal"),
            label=e.get("label", ""),
        ))

    all_edges = timeline_edges + narrative_edges
    return {"edges": all_edges}
