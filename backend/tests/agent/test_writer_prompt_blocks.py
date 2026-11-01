"""writer.yaml 的每个区块，在每个 action 下都**必须真的渲染出来**。

这一类事故是 2026-09-13 踩出来的，而且 853 条测试一条都没抓到：

    {% if existing_content and (action|default("")) == "continue" %}
      ... {{existing_content_tail}}
    {% endif %}

``existing_content`` 在这里只是拿来做"有没有正文"的判断。而``build_for_writing``
后来收紧了（write 不给正文、continue 只给 tail），``existing_content`` 就从
continue 的上下文里消失了 —— **Jinja 把未定义当假，整块静默消失**。
后果是续写时写手看不到该从哪接，功能等于废了，而所有断言都是绿的。

为什么抓不到：那些测试只检查 ``ctx`` 字典里有没有某个键，**不检查它有没有进
渲染结果**。ctx 里有、提示词里没有，在任何字典断言看来都是对的。

所以这里的做法是：**用真实的 build_for_writing 产物渲染一遍，逐个区块断言它在
该出现的 action 下确实出现了。** 不扫模板源码猜守卫（守卫行里也会出现变量名，
但那只当条件用），也不手动往 render() 里塞键（那正是漏掉真实守卫的原因）。
"""
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ContextBuilder
from app.agent.prompts import PromptLoader
from app.project.repository import ProjectRepository
from app.storycad.models import Act, Chapter, Character, Setup
WRITER_YAML = (Path(__file__).resolve().parents[2]
               / "app" / "agent" / "prompts" / "writer.yaml")


# 区块标题 → 哪些 action 下必须出现
# 一条都不写"所有 action"：不同 action 的材料本来就不同。
# 每条都对应 prompt 里一个真实存在的区块，标题写错会让本文件自己先失败。
BLOCKS: dict[str, set[str]] = {
    "## 当前这一章":            {"write", "continue", "rewrite"},
    "## 本章的伏笔":            {"write", "continue", "rewrite"},
    "## 上一章（**本章的起点**）": {"write", "continue", "rewrite"},
    "## 人物":                  {"write", "continue", "rewrite"},
    "# ——— 怎么演这一章 ———":     {"write", "continue", "rewrite"},
    "## 蓝图怎么用（先读这一节）":   {"write", "continue", "rewrite"},
    "## 已有正文（续写点）":       {"continue"},
    "## 待重写的原文":           {"rewrite"},
}

# 这些在夹具里被刻意留空，所以任何 action 下都不该出现
NEVER: list[str] = [
    "## 还没回收的伏笔",        # 旧的、含糊的伏笔标题
    "（这一章可能轮到它们",      # 旧的免责话术
    "{{",                     # 任何未取值的占位符
    "}}",
]


async def _project(db_session: AsyncSession, test_user: dict) -> dict:
    project = await ProjectRepository(db_session).create("Render", "", test_user["id"])
    project.spine = "当前已完成骨架"
    project.genre = "悬疑"
    project.logline = "一句话"
    project.global_settings = "小镇"
    await db_session.flush()

    act = Act(id=uuid.uuid4(), project_id=project.id, name="第一幕",
              purpose="把他扣在镇上", sort_order=1)
    db_session.add(act)

    prev = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                   title="第一章", goal="陈默翻出账本",
                   content="上一章正文停在这里。", status="final", sort_order=1)
    target = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                     title="第二章", goal="陈默去杂货铺问老板。",
                     content="本章已有正文，用来试续写与重写。" * 200,
                     status="revising", sort_order=2)
    nxt = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act.id,
                  title="第三章", goal="他决定留下。", content="", sort_order=3)
    db_session.add_all([prev, target, nxt])

    db_session.add(Character(id=uuid.uuid4(), project_id=project.id, name="陈默",
                             role="main", appearance="瘦高个", motivation="查旧债",
                             arc="从局外人到留下的人", sort_order=1))
    db_session.add(Setup(id=uuid.uuid4(), project_id=project.id, note="缺页的账本",
                         planted_in=prev.id, pay_off_in=target.id, status="open"))
    await db_session.flush()
    return {"project": project, "act": act, "prev": prev, "target": target}


def _template_headings() -> set[str]:
    text = yaml.safe_load(WRITER_YAML.read_text(encoding="utf-8"))["system"]
    return {ln.strip() for ln in text.splitlines() if ln.strip().startswith("#")}


def test_block_table_matches_the_template():
    """BLOCKS 里不能有模板里已经不存在的标题——否则本文件会静默失去作用。"""
    headings = _template_headings()
    for title in BLOCKS:
        assert any(title in h for h in headings), f"模板里没有 {title}"


@pytest.mark.parametrize("action", ["write", "continue", "rewrite"])
async def test_every_block_renders_in_its_actions(
    db_session: AsyncSession, test_user: dict, action: str,
):
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, action)
    ctx.update({"action": action, "persona": "你是一位资深中文小说家"})

    rendered = PromptLoader().load("writer").render(**ctx)

    for title, actions in BLOCKS.items():
        if action in actions:
            assert title in rendered, f"{action}: {title} 没渲染出来"
        else:
            assert title not in rendered, f"{action}: {title} 不该出现"

    for bad in NEVER:
        assert bad not in rendered, f"{action}: 出现了不该有的 {bad!r}"


@pytest.mark.parametrize("action", ["write", "continue", "rewrite"])
async def test_rendered_prompt_carries_the_real_material(
    db_session: AsyncSession, test_user: dict, action: str,
):
    """每个 action 下，喂进去的真材料都得真的出现在提示词里。

    这条盯的是"ctx 里有、提示词里没有"——那个 bug 让续写功能整块失效。
    """
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, action)
    ctx.update({"action": action, "persona": "作家"})
    rendered = PromptLoader().load("writer").render(**ctx)

    # 三种动作都该有的
    for must in ("陈默去杂货铺问老板",     # 本章蓝图
                 "瘦高个",                   # 人物外貌
                 "缺页的账本",               # 本章要收的伏笔
                 "上一章正文停在这里。"):      # 上一章结尾原文
        assert must in rendered, f"{action}: {must!r} 没进提示词"


async def test_a_guard_on_an_absent_variable_does_not_silently_drop_the_block(
    db_session: AsyncSession, test_user: dict,
):
    """反向证明：守卫变量缺失 → 块消失，而测试确实能看见这件事。

    直接把 continue 要用的 ``existing_content_tail`` 从上下文里拿掉：如果本文件
    前面那些断言有效，这里渲染出来的续写块就该消失。这条是给未来的人看的
    ——它说明整套断言不是摆设，块真的会因为变量缺失而不见。
    """
    f = await _project(db_session, test_user)
    ctx = await ContextBuilder(db_session).build_for_writing(f["target"].id, "continue")
    ctx.update({"action": "continue", "persona": "作家"})
    loader = PromptLoader().load("writer")

    assert "## 已有正文（续写点）" in loader.render(**ctx)

    del ctx["existing_content_tail"]
    assert "## 已有正文（续写点）" not in loader.render(**ctx), (
        "守卫变量缺失时块会消失——所以上面那些断言必须在渲染结果上做"
    )
