"""``ContextBuilder.build_for_writing`` —— 交给写作智能体的专注上下文（**按章**）。

本文件守住两件历史上真的出过事故的事：

1. **上下文不许再装场景层**。写作单位是章：``build_for_writing(chapter_id, action)``
   只装这一章要写什么、它在整本书里的位置、上一章的蓝图与结尾原文、人物此刻要什么、
   还没收的伏笔、已有正文。视角没有字段（写在章蓝图里）。跨章连续性靠上一章的
   **逐字结尾**（不是摘要——摘要是会过期又不重算的派生字段，迁移 0022 已删除）。
2. **模板变量与上下文字段必须一致**。历史上 ``writer.yaml`` 引用了
   ``build_for_writing`` 从不提供的 ``tone``，而 ``build_for_writing`` 又拼了模板
   从不使用的 ``related_edges`` / ``available_skills`` —— 每次写作白查两次库。
   这条用例就是为那次事故留的哨兵：模板要的变量必须全给，给的字段必须全被用。
"""
import uuid
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment
from jinja2.meta import find_undeclared_variables
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ContextBuilder
from app.agent.prompts import PromptLoader
from app.project.repository import ProjectRepository
from app.storycad.models import Act, Chapter, Character, Setup

WRITER_YAML = (Path(__file__).resolve().parents[2]
               / "app" / "agent" / "prompts" / "writer.yaml")

# 不是 build_for_writing 产出、但渲染时一定会有的变量：
#   persona          —— WritingAgent 渲染 persona_writer 后传入
#   action           —— CallWriterAgentTool 传进 build_for_writing 的动作
#   user_requirement —— 用户这一轮的要求（可能为空）
CALLER_SUPPLIED = {"persona", "action", "user_requirement"}

# 只在特定 action 下才会被 Jinja2 取值的变量。
# write 是整体替换，build_for_writing 故意不提供续写点（模板里的 if 会短路，
# 未取值的变量不会报错）。continue / rewrite 必须全都给到。
ACTION_CONDITIONAL = {
    "write": {"existing_content_tail"},
    "continue": set(),
    "rewrite": set(),
}

# 由**项目状态**（而不是 action）决定在不在的变量：最后一章没有"下一章"，
# 上一章可能没写蓝图（那就没有"上一章要写什么"）。
# 豁免必须有正向用例兜着（test_the_next_chapter_is_given_only_as_a_stopping_point、
# test_tail_widens_when_the_previous_chapter_has_no_blueprint），
# 否则这张表就成了掩盖 bug 的后门。
STATE_OPTIONAL = {
    "next_chapter_title",
    "next_chapter_goal",
    "previous_chapter_goal",
}

# build_for_writing 会多装、模板目前不用的三个键。
#
# 历史上"白查两次库"的正是这一类多余负载，所以刻意把它点名列出来。
# 现在**一个都没有**——上下文里的每个键 writer.yaml 都会用到。
# 名单留空但仍要断言，这样以后再加多余键会被拦住，而不是悄悄堆在上下文里。
UNUSED_CTX_KEYS: set[str] = set()

PREV_GOAL = "上一章要写什么：陈默在姑妈家翻出账本，姑妈改了口。"
CHAPTER_GOAL = "本章要写什么：陈默连夜去杂货铺问老板。"
CHAPTER_BODY = "陈默推门进去，老板正在擦杯子。" * 5


def _template_vars() -> set[str]:
    """writer.yaml 里 Jinja2 模板真正引用的变量。"""
    text = yaml.safe_load(WRITER_YAML.read_text(encoding="utf-8"))["system"]
    return set(find_undeclared_variables(Environment().parse(text)))


async def _project(db_session: AsyncSession, test_user: dict) -> dict:
    project = await ProjectRepository(db_session).create("WriterCtx", "", test_user["id"])
    project.spine = "主线骨架：奔丧 → 发现账本缺页 → 姑妈改口 → 陈默留下"
    project.ending = "终局：陈默留下查清旧债"
    project.genre = "悬疑"
    project.logline = "一句话梗概：一个外人的丧事变成他的旧案"
    project.global_settings = "小镇名叫渡口，杂货铺是唯一还开着的店。"
    await db_session.flush()

    act = Act(id=uuid.uuid4(), project_id=project.id, name="第一幕",
              purpose="第一幕要干什么：把他扣在镇上", sort_order=1)
    db_session.add(act)

    prev = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                   goal=PREV_GOAL,
                   title="第一章",
                   content="上一章正文的结尾段落。", status="final", sort_order=1)
    target = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                     goal=CHAPTER_GOAL, title="第二章",
                     content=CHAPTER_BODY, status="revising", sort_order=2)
    db_session.add_all([prev, target])

    hero = Character(id=uuid.uuid4(), project_id=project.id, name="陈默", role="main",
                     appearance="瘦高个，校服洗得发白，眼神比同龄人老成",
                     personality="克制", motivation="查清父亲留下的债",
                     arc="从局外人到留下来的人", background="十年没回过镇上",
                     sort_order=1)
    db_session.add(hero)
    await db_session.flush()

    open_setup = Setup(id=uuid.uuid4(), project_id=project.id, note="缺了几页的账本",
                       planted_in=prev.id, pay_off_in=target.id, status="open")
    paid_setup = Setup(id=uuid.uuid4(), project_id=project.id, note="已经收了的旧硬币",
                       planted_in=prev.id, pay_off_in=None, status="paid")
    db_session.add_all([open_setup, paid_setup])
    await db_session.flush()

    return {"project": project, "act": act, "prev": prev, "target": target, "hero": hero}


# ── 按章、且只按章 ────────────────────────────────────────────────────────────

async def test_context_is_chapter_scoped_and_carries_no_scene_layer(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    leaked = sorted(key for key in ctx if "scene" in key or "pov" in key or "edge" in key)
    assert not leaked, f"场景层已删除，上下文里不该再有这些键：{leaked}"

    assert ctx["chapter_title"] == "第二章"
    assert ctx["chapter_goal"] == CHAPTER_GOAL
    assert ctx["chapter_number"] == 2, "全局章号是算出来的"
    assert ctx["act_name"] == "第一幕"
    assert ctx["act_purpose"] == "第一幕要干什么：把他扣在镇上"


async def test_unknown_chapter_yields_an_empty_context(db_session: AsyncSession, test_user: dict):
    await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(uuid.uuid4(), "write")
    assert ctx == {}


# ── 跨章连续性：上一章蓝图 + 逐字结尾 ──────────────────────────────────────────

async def test_previous_chapter_blueprint_and_tail_are_the_starting_point(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert ctx["previous_chapter_title"] == "第一章"
    assert ctx["previous_chapter_goal"] == PREV_GOAL
    # 起点是**逐字原文**，不是任何摘要。用户改一个字它就跟着变。
    assert ctx["previous_chapter_tail"] == "上一章正文的结尾段落。"
    assert "previous_chapter_result" not in ctx, "「本章写成了什么」已删除（迁移 0022）"


async def test_tail_widens_when_the_previous_chapter_has_no_blueprint(db_session: AsyncSession, test_user: dict):
    """上一章没写蓝图时只剩逐字原文可依——800 字接不住一次自由发挥的章。

    这是删掉 result 之后唯一的降级路径：没有摘要兜底，就得给原文更多字。
    """
    f = await _project(db_session, test_user)
    long_tail = "陈默把手按在柜台上。" * 400        # 远超 800 字
    f["prev"].goal = ""
    f["prev"].content = long_tail
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert "previous_chapter_goal" not in ctx
    tail = ctx["previous_chapter_tail"]
    assert len(tail) == 2000, "无蓝图时 tail 放宽到 2000 字"
    assert long_tail.endswith(tail), "取的仍是结尾"


async def test_tail_keeps_800_chars_when_the_previous_chapter_has_a_blueprint(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    long_tail = "陈默把手按在柜台上。" * 400
    f["prev"].content = long_tail
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert ctx["previous_chapter_goal"] == PREV_GOAL
    assert len(ctx["previous_chapter_tail"]) == 800


async def test_the_first_chapter_has_no_previous_chapter_keys(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["prev"].id, "write")

    assert ctx["chapter_number"] == 1
    assert "previous_chapter_goal" not in ctx
    assert "previous_chapter_title" not in ctx
    assert "previous_chapter_tail" not in ctx


# ── 整本书里的位置 ────────────────────────────────────────────────────────────

async def test_position_in_the_book_is_included(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert ctx["project_title"] == "WriterCtx"
    assert ctx["genre"] == "悬疑"
    assert ctx["logline"].startswith("一句话梗概")
    assert "主线骨架" in ctx["spine"], "主线骨架是判断「该往哪使劲」的依据"
    assert "终局" in ctx["ending"]
    assert "渡口" in ctx["global_settings"]


async def test_characters_text_carries_want_and_arc(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    text = ctx["characters_text"]
    assert "陈默" in text
    assert "他想要什么：查清父亲留下的债" in text
    assert "他从哪到哪：从局外人到留下来的人" in text


async def test_characters_text_carries_appearance(db_session: AsyncSession, test_user: dict):
    """外貌必须进正文材料：先前只给性格/动机/弧光/背景，每一章的长相都是写手现编的。"""
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert "外貌：瘦高个" in ctx["characters_text"]


async def test_empty_character_list_says_so(db_session: AsyncSession, test_user: dict):
    project = await ProjectRepository(db_session).create("NoChar", "", test_user["id"])
    act = Act(id=uuid.uuid4(), project_id=project.id, name="第一幕", purpose="甲", sort_order=1)
    db_session.add(act)
    chapter = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                      title="第一章", goal="要写什么", sort_order=1)
    db_session.add(chapter)
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(chapter.id, "write")
    assert ctx["characters_text"] == "暂无人物"


# ── 还没收的伏笔 ──────────────────────────────────────────────────────────────

async def test_open_setups_text_lists_only_unpaid_setups(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    text = ctx["open_setups_text"]
    assert "缺了几页的账本" in text
    assert "已经收了的旧硬币" not in text, "已收的伏笔不该再提醒写手"
    assert "第 1 章埋下" in text
    assert "打算第 2 章收" in text


async def test_no_open_setups_means_no_key_at_all(db_session: AsyncSession, test_user: dict):
    project = await ProjectRepository(db_session).create("NoSetup", "", test_user["id"])
    act = Act(id=uuid.uuid4(), project_id=project.id, name="第一幕", purpose="甲", sort_order=1)
    db_session.add(act)
    chapter = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                      title="第一章", goal="要写什么", sort_order=1)
    db_session.add(chapter)
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(chapter.id, "write")
    assert "open_setups_text" not in ctx


async def test_a_setup_without_a_pay_off_chapter_says_so(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    f["project"].spine = f["project"].spine  # 保持项目不变，只加一条没有回收计划的伏笔
    db_session.add(Setup(id=uuid.uuid4(), project_id=f["project"].id, note="没定在哪收的东西",
                         planted_in=f["prev"].id, pay_off_in=None, status="open"))
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")
    assert "没定在哪收的东西（第 1 章埋下）（还没定在哪收）" in ctx["open_setups_text"]


# ── 已有正文与 action ─────────────────────────────────────────────────────────

async def test_existing_content_is_given_for_write_and_rewrite(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert ctx["existing_content"] == CHAPTER_BODY
    assert "existing_content_tail" not in ctx, "write 是整体替换，不需要续写点"
    # 正文字数不给写手：它对写手没有信息量（正文本身就在 existing_content 里），
    # 多一个键就是一份会和模板悄悄漂移的载荷。UNUSED_CTX_KEYS 为空即为此。
    assert "chapter_body_chars" not in ctx
    assert "chapter_status" not in ctx
    assert "chapter_result" not in ctx, "「本章写成了什么」已删除（迁移 0022）"


async def test_continue_and_rewrite_get_the_tail(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    for action in ("continue", "rewrite"):
        ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, action)
        assert ctx["existing_content_tail"] == CHAPTER_BODY[-1500:], action


async def test_a_chapter_without_prose_has_no_existing_content(db_session: AsyncSession, test_user: dict):
    project = await ProjectRepository(db_session).create("Empty", "", test_user["id"])
    act = Act(id=uuid.uuid4(), project_id=project.id, name="第一幕", purpose="甲", sort_order=1)
    db_session.add(act)
    chapter = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                      title="第一章", goal="要写什么", content="", sort_order=1)
    db_session.add(chapter)
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(chapter.id, "continue")
    assert "existing_content" not in ctx
    assert "existing_content_tail" not in ctx
    assert "chapter_body_chars" not in ctx


# ── 提示词 / 上下文变量一致性（历史上真出过 bug 的那条）────────────────────────

@pytest.mark.parametrize("action", ["write", "continue", "rewrite"])
async def test_writer_template_variables_are_all_supplied(db_session: AsyncSession, test_user: dict, action: str):
    """writer.yaml 引用的每一个变量，都必须由 build_for_writing（或调用方）提供。"""
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, action)

    missing = (_template_vars() - set(ctx) - CALLER_SUPPLIED
               - ACTION_CONDITIONAL[action] - STATE_OPTIONAL)
    assert not missing, f"writer.yaml 引用了没人提供的变量：{sorted(missing)}"


async def test_writer_context_keys_are_all_used_by_the_template(db_session: AsyncSession, test_user: dict):
    """反方向：上下文里装的字段必须真的被模板用到（否则就是每次写作白查库）。"""
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "continue")

    unused = set(ctx) - _template_vars()
    assert unused == UNUSED_CTX_KEYS, (
        f"多出来的上下文键变了：{sorted(unused)}；"
        f"已知的多余键是 {sorted(UNUSED_CTX_KEYS)}"
    )


@pytest.mark.parametrize("action", ["write", "continue", "rewrite"])
async def test_the_prompt_renders_without_leftover_placeholders(
    db_session: AsyncSession, test_user: dict, action: str
):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, action)
    ctx.update({"action": action, "persona": "你是一位资深中文小说家"})

    rendered = PromptLoader().load("writer").render(**ctx)
    assert "{{" not in rendered
    assert "}}" not in rendered


async def test_the_new_variable_set_actually_reaches_the_prompt(db_session: AsyncSession, test_user: dict):
    """spine / ending / previous_chapter_goal / characters_text / open_setups_text
    这五个是这次重构新增或改名的，缺一个都会让写手失去判断依据。"""
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")
    ctx.update({"action": "write", "persona": "你是一位资深中文小说家"})
    rendered = PromptLoader().load("writer").render(**ctx)

    assert ctx["spine"] in rendered
    assert ctx["ending"] in rendered
    assert PREV_GOAL in rendered
    assert "上一章正文的结尾段落。" in rendered, "逐字结尾是接缝的唯一来源"
    assert "他想要什么：查清父亲留下的债" in rendered
    assert "缺了几页的账本" in rendered

    # 章是写作单位，提示词里不再有场景层的说法
    assert "## 当前这一章" in rendered
    assert "场景" not in rendered


# ── 篇幅参考：写手原来只拿到「由这一章的分量决定」────────────────────────────

async def test_length_target_uses_the_average_of_written_chapters(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    f["prev"].content = "上一章正文。" * 400          # 2400 字
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    # 平均 2400 → 下限 0.8 倍 = 1920、上限 1.35 倍 = 3240，都取整到百位
    assert "1900–3200 字" in ctx["length_target"]
    assert "本书已写 1 章，平均 2400 字" in ctx["length_target"]
    assert "写到 1900 字以下" in ctx["length_target"]


async def test_length_target_falls_back_when_nothing_is_written(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    f["prev"].content = ""
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert "2500–4000 字" in ctx["length_target"]
    assert "还没有正文" in ctx["length_target"]


async def test_length_target_ignores_the_chapter_being_written(db_session: AsyncSession, test_user: dict):
    """本章自己的正文不能算进"本书平均"——否则重写时会自己锚定自己。"""
    f = await _project(db_session, test_user)
    f["prev"].content = ""
    f["target"].content = "本章已经写过的正文。" * 400     # 4000 字
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "rewrite")

    assert "2500–4000 字" in ctx["length_target"]


# ── 下一章：只当停点用 ───────────────────────────────────────────────────────

async def test_the_next_chapter_is_given_only_as_a_stopping_point(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    db_session.add(Chapter(id=uuid.uuid4(), project_id=f["project"].id, act_id=f["act"].id,
                           title="第三章", goal="他决定留下。", sort_order=3))
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert ctx["next_chapter_title"] == "第三章"
    assert ctx["next_chapter_goal"] == "他决定留下。"


async def test_the_last_chapter_has_no_next_chapter_keys(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert "next_chapter_title" not in ctx
    assert "next_chapter_goal" not in ctx


# ── 人物档案：不再硬切在半句话上，名单一个都不丢 ──────────────────────────────

async def test_character_roles_are_rendered_in_chinese(db_session: AsyncSession, test_user: dict):
    """写手不该在提示词里读到 main / supporting 这种字段值。"""
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert "- 陈默（主角）" in ctx["characters_text"]


async def test_long_character_fields_are_cut_at_a_sentence_end(db_session: AsyncSession, test_user: dict):
    """硬切的后果是写手读到「……骨子里有」这种半句话，只能自己补完。"""
    f = await _project(db_session, test_user)
    f["hero"].personality = "谨慎。" * 200            # 600 字，超过 320 的额度
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")
    line = next(row for row in ctx["characters_text"].splitlines() if row.startswith("  性格："))

    assert line.endswith("……（后文略）"), line
    body = line[: -len("……（后文略）")]
    assert body.endswith("。"), "截断必须落在句末标点上，不能切断半句话"
    assert len(body) <= 320 + len("  性格：")


async def test_short_character_fields_get_no_marker(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert "性格：克制" in ctx["characters_text"]
    assert "后文略" not in ctx["characters_text"]


async def test_character_names_are_never_dropped(db_session: AsyncSession, test_user: dict):
    """档案超预算时只给名单——名单丢了，写手就会自己造一个人顶上去。"""
    f = await _project(db_session, test_user)
    for i in range(30):
        db_session.add(Character(id=uuid.uuid4(), project_id=f["project"].id,
                                 name=f"配角{i}", role="supporting",
                                 personality="小心。" * 200, motivation="活下去",
                                 sort_order=10 + i))
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")
    text = ctx["characters_text"]

    assert "- 陈默（主角）" in text, "排在前面的档案要留住"
    assert "只列了名字" in text, "裁掉了谁必须说清楚"
    assert "配角29" in text, "名单一个都不能丢"


# ── 世界观：预算变大，被截断时必须说出来 ─────────────────────────────────────

async def test_a_huge_world_bible_is_cut_with_an_honest_marker(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    f["project"].global_settings = "设定。" * 4000     # 12000 字
    await db_session.flush()

    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")
    text = ctx["global_settings"]

    assert text.endswith("……（后文略）")
    assert len(text) <= 8000 + len("……（后文略）")


async def test_a_short_world_bible_is_given_whole(db_session: AsyncSession, test_user: dict):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "write")

    assert ctx["global_settings"] == "小镇名叫渡口，杂货铺是唯一还开着的店。"


def test_writer_yaml_references_no_scene_or_dead_variables():
    text = WRITER_YAML.read_text(encoding="utf-8")
    for dead in ("related_edges", "available_skills", "tone", "user_prompt",
                 "pov_character", "scene_title", "scene_summary",
                 "previous_scene_tail", "chapter_scenes_framework"):
        assert dead not in text, f"writer.yaml 不该引用 {dead}"
