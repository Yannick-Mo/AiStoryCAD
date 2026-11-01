"""收口：给生成结果加护栏，并且**绝不静默截断**。

两本账，各有一份消费者：

* ``errors``     —— 给人看的流水线报告（SSE 每一步的预览读它）。修正类的也记在这里。
* ``truncation`` —— 被**主动丢掉**的数据（中文说明），writer 会把它原样并进返回值的
                    ``truncated``，所以调用方一定看得见。截断不是"清理"，是"丢设计"，
                    必须记账（规格第 11 节：静默截断是这个项目最该根除的坏习惯）。

上限只是**跑飞保护**：模板只要求开头这一段（1-2 幕、4-10 章）、3-8 个人物，
下面的数字远在其上，正常生成根本碰不到；碰到了就如实报出砍了几幕几章。
"""
from loguru import logger

from app.agent.project_creator.state import MaterialState, as_int, as_text
from app.agent.utils import GenerationError

MAX_ACTS = 8
MAX_CHAPTERS = 60
MAX_CHAPTERS_PER_ACT = 25
MAX_CHARACTERS = 20
MAX_RELATIONS = 40


def _correct(errors: list[str], msg: str) -> None:
    """修正类：数据没丢，只是被补全/改写了。只进 errors。"""
    errors.append(msg)


def _drop(errors: list[str], truncation: list[str], msg: str) -> None:
    """丢数据类：这条数据不会写库（或写了但不完整）。errors 与 truncated 都要有。"""
    errors.append(msg)
    truncation.append(msg)


async def validate(state: MaterialState) -> dict:
    errors: list[str] = list(state.get("errors") or [])
    truncation: list[str] = []

    raw_acts = state.get("acts") or []
    acts = [dict(a) if isinstance(a, dict) else {} for a in raw_acts]
    if not acts:
        # 没有幕就没有章的归属（chapters.act_id NOT NULL），项目无从写库。
        # 与其写一个空壳项目，不如在这里大声失败，让调用方重跑结构规划。
        raise GenerationError(
            node_name="validate",
            message="结构规划没有产出任何幕：没有幕就没有章的归属，项目无法写库",
        )

    if len(acts) > MAX_ACTS:
        cut = len(acts) - MAX_ACTS
        _drop(
            errors, truncation,
            f"幕数 {len(acts)} 超过上限 {MAX_ACTS}，已砍掉后 {cut} 幕（连带这些幕下的章）",
        )
        acts = acts[:MAX_ACTS]

    for index, act in enumerate(acts):
        if not as_text(act.get("name")):
            _correct(errors, f"第{index + 1}幕没有幕名，写库时用「第{index + 1}幕」代替")
        if not as_text(act.get("purpose")):
            _correct(errors, f"第{index + 1}幕没有「这一幕要干什么」（purpose），动笔前要补上")

    raw_chapters = [c for c in (state.get("chapters") or []) if isinstance(c, dict)]
    chapters = [dict(c) for c in raw_chapters]

    if len(chapters) > MAX_CHAPTERS:
        cut = len(chapters) - MAX_CHAPTERS
        _drop(errors, truncation, f"章数 {len(chapters)} 超过上限 {MAX_CHAPTERS}，已砍掉后 {cut} 章")
        chapters = chapters[:MAX_CHAPTERS]

    # 幕内章数上限：按章在列表里的先后保留前 MAX_CHAPTERS_PER_ACT 章。
    # act_index 越界的章不在这里处理——writer 会按"幕序号超出范围"如实报出。
    seen_per_act: dict[int, int] = {}
    cut_per_act: dict[int, int] = {}
    kept: list[dict] = []
    for chapter in chapters:
        act_index = as_int(chapter.get("act_index"), -1)
        if not 0 <= act_index < len(acts):
            kept.append(chapter)
            continue
        seen_per_act[act_index] = seen_per_act.get(act_index, 0) + 1
        if seen_per_act[act_index] > MAX_CHAPTERS_PER_ACT:
            cut_per_act[act_index] = cut_per_act.get(act_index, 0) + 1
            continue
        kept.append(chapter)
    chapters = kept
    for act_index, cut in sorted(cut_per_act.items()):
        _drop(
            errors, truncation,
            f"第{act_index + 1}幕的章数超过上限 {MAX_CHAPTERS_PER_ACT}，已砍掉后 {cut} 章",
        )

    if not chapters:
        _drop(errors, truncation, "结构规划没有产出任何章：项目只有幕，没有可以写的章")

    missing = [c for c in chapters if not as_text(c.get("goal"))]
    if missing:
        titles = "、".join(f"《{as_text(c.get('title')) or '未命名'}》" for c in missing[:5])
        more = "等" if len(missing) > 5 else ""
        _drop(
            errors, truncation,
            f"{len(missing)} 章没有拿到章蓝图（{titles}{more}）：这些章仍会写库，"
            f"但「本章要写什么」是空的，动笔前必须补上",
        )

    raw_characters = [c for c in (state.get("characters") or []) if isinstance(c, dict)]
    characters = [dict(c) for c in raw_characters]

    nameless = [c for c in characters if not as_text(c.get("name"))]
    if nameless:
        _drop(errors, truncation, f"{len(nameless)} 个人物没有姓名，已丢弃（人物必须有名字才能建行）")
        characters = [c for c in characters if as_text(c.get("name"))]

    if len(characters) > MAX_CHARACTERS:
        cut = len(characters) - MAX_CHARACTERS
        _drop(errors, truncation, f"人物数 {len(characters)} 超过上限 {MAX_CHARACTERS}，已砍掉后 {cut} 个")
        characters = characters[:MAX_CHARACTERS]

    relations = [dict(r) for r in (state.get("relations") or []) if isinstance(r, dict)]
    if len(relations) > MAX_RELATIONS:
        cut = len(relations) - MAX_RELATIONS
        _drop(errors, truncation, f"角色关系数 {len(relations)} 超过上限 {MAX_RELATIONS}，已砍掉后 {cut} 条")
        relations = relations[:MAX_RELATIONS]

    if truncation:
        logger.warning("validate: 本次生成有 {} 项数据没有完整落地：{}", len(truncation), truncation)

    return {
        "acts": acts,
        "chapters": chapters,
        "characters": characters,
        "relations": relations,
        "errors": errors,
        "truncation": truncation,
    }
