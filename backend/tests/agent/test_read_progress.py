"""``read_progress`` / ``ContextBuilder.build_progress`` —— 进度是**算出来的清单，不是表**。

它同时取代了四个旧工具（规格第 10 节）：``suggest_next``、``project_health``、
``read_recent_chapters`` / ``read_recent_scenes``、``read_completed_tail``。
所以本文件接替了 ``test_analysis_tools.py``（那个文件测的
``analyze_chapter`` / ``analyze_character_arc`` / ``suggest_next`` / ``project_health``
四个工具全部删除，模块 ``analysis_v2_tools.py`` 也已不存在）。

清单必须能被消费：设计缺什么、正文断点在第几章、伏笔逾期、定稿进度、最近改动的章、下一步。
"""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ContextBuilder
from app.project.repository import ProjectRepository
from app.storycad.models import Act, Chapter, Character, Setup


pytestmark = pytest.mark.asyncio


BODY = "正文正文正文。"


async def _progress_fixture(db_session: AsyncSession, test_user: dict) -> dict:
    """一个刻意留了各种缺口的项目，用来逐条验收清单。

    幕：
      * 第一幕 purpose 齐全
      * 第二幕 purpose 为空            → design_gaps「幕没写要干什么」

    章（全局章号 1..5）：
      * 1 第一章：有正文、有 goal       → 无缺口
      * 2 第二章：有正文、有 goal       → 无缺口（「本章写成了什么」已删除）
      * 3 第三章：没有正文、有 goal     → content_break（第一个正文为空的章）
      * 4 第四章：goal 为空            → design_gaps「章没写要写什么」
      * 5 第五章：有 goal、没有正文

    人物：主角（有 motivation）· 配角（motivation 为空 → design_gaps）
    伏笔：
      * 旧硬币：planted 第1章、pay_off 第2章、open —— 第2章已写 → overdue
      * 钥匙：  planted 第1章、pay_off 第3章、open —— 第3章未写 → 只是 open
      * 一封信：planted 第1章、pay_off 空、  open
      * 刀：    planted 第1章、pay_off 第2章、paid —— 已收 → 既不在 open 也不 overdue
    """
    project = await ProjectRepository(db_session).create("Progress", "", test_user["id"])

    act1 = Act(project_id=project.id, name="第一幕", purpose="第一幕要干什么", sort_order=1)
    act2 = Act(project_id=project.id, name="第二幕", purpose="", sort_order=2)
    db_session.add_all([act1, act2])
    await db_session.flush()

    def chapter(act, title, goal, content, status, order):
        return Chapter(project_id=project.id, act_id=act.id, title=title, goal=goal,
                       content=content, status=status, sort_order=order)

    ch1 = chapter(act1, "第一章", "要写什么", BODY, "final", 1)
    ch2 = chapter(act1, "第二章", "要写什么", BODY, "revising", 2)
    ch3 = chapter(act1, "第三章", "要写什么", "", "draft", 3)
    ch4 = chapter(act2, "第四章", "", "", "draft", 1)
    ch5 = chapter(act2, "第五章", "要写什么", "", "draft", 2)
    db_session.add_all([ch1, ch2, ch3, ch4, ch5])

    hero = Character(project_id=project.id, name="主角", role="main",
                     motivation="复仇", arc="从隐忍到摊牌", sort_order=1)
    side = Character(project_id=project.id, name="配角", role="supporting",
                     motivation="", arc="", sort_order=2)
    db_session.add_all([hero, side])
    await db_session.flush()

    setups = {
        "旧硬币": Setup(project_id=project.id, note="旧硬币", planted_in=ch1.id,
                       pay_off_in=ch2.id, status="open"),
        "钥匙": Setup(project_id=project.id, note="钥匙", planted_in=ch1.id,
                     pay_off_in=ch3.id, status="open"),
        "一封信": Setup(project_id=project.id, note="一封信", planted_in=ch1.id,
                      pay_off_in=None, status="open"),
        "刀": Setup(project_id=project.id, note="刀", planted_in=ch1.id,
                   pay_off_in=ch2.id, status="paid"),
    }
    db_session.add_all(list(setups.values()))
    await db_session.flush()

    return {"project": project, "acts": [act1, act2], "chapters": [ch1, ch2, ch3, ch4, ch5],
            "characters": [hero, side], "setups": setups}


def _gap_kinds(progress: dict) -> list[str]:
    return [gap["what"] for gap in progress["design_gaps"]]


# ── 计数与状态 ────────────────────────────────────────────────────────────────

async def test_counts_and_status_breakdown(db_session: AsyncSession, test_user: dict):
    f = await _progress_fixture(db_session, test_user)
    progress = await ContextBuilder(db_session).build_progress(f["project"].id)

    assert progress["project_id"] == str(f["project"].id)
    assert progress["act_count"] == 2
    assert progress["chapter_count"] == 5
    assert progress["written_chapter_count"] == 2, "只有第 1、2 章有正文"
    assert progress["status_counts"] == {"draft": 3, "revising": 1, "final": 1}


# ── 正文断点 ──────────────────────────────────────────────────────────────────

async def test_content_break_points_at_the_first_chapter_with_an_empty_body(db_session: AsyncSession, test_user: dict):
    f = await _progress_fixture(db_session, test_user)
    progress = await ContextBuilder(db_session).build_progress(f["project"].id)

    break_point = progress["content_break"]
    assert break_point is not None, "第 3 章是空的，必须报出断点"
    assert break_point["chapter_id"] == str(f["chapters"][2].id)
    assert break_point["global_order"] == 3, "断点用的是全局章号，不是幕内序号"
    assert break_point["title"] == "第三章"
    assert break_point["act_name"] == "第一幕"


async def test_chapter_window_and_progress_agree_on_body_presence(db_session: AsyncSession, test_user: dict):
    """「有没有正文」只有一个定义：正文字符数 > 0。两个只读表面必须一致。"""
    f = await _progress_fixture(db_session, test_user)
    builder = ContextBuilder(db_session)
    progress = await builder.build_progress(f["project"].id)
    window = await builder.build_chapter_window(f["project"].id, 1, 5)

    by_order = {entry["global_order"]: entry for entry in window["chapters"]}
    for entry in by_order.values():
        assert entry["body_chars"] > 0 if entry["global_order"] <= 2 else entry["body_chars"] == 0

    written = {e["global_order"] for e in by_order.values() if e["body_chars"] > 0}
    assert written == {1, 2}
    assert progress["written_chapter_count"] == len(written)


# ── 设计缺口 ──────────────────────────────────────────────────────────────────

async def test_design_gaps_report_every_missing_field(db_session: AsyncSession, test_user: dict):
    f = await _progress_fixture(db_session, test_user)
    progress = await ContextBuilder(db_session).build_progress(f["project"].id)
    kinds = _gap_kinds(progress)

    assert any("幕没写" in kind for kind in kinds), kinds
    assert any("章没写" in kind and "要写什么" in kind for kind in kinds), kinds
    assert any("人物没写" in kind for kind in kinds), kinds
    # 曾经还有一条「章有正文但没写『本章写成了什么』」。它现在**不该存在**：
    # 下一章直接读上一章结尾原文，没有需要写回的字段（迁移 0022）。
    assert not any("有正文但没写" in kind for kind in kinds), kinds


async def test_design_gaps_carry_the_id_and_the_fixing_tool(db_session: AsyncSession, test_user: dict):
    f = await _progress_fixture(db_session, test_user)
    progress = await ContextBuilder(db_session).build_progress(f["project"].id)

    by_id = {}
    for gap in progress["design_gaps"]:
        assert gap["fix_with"] in {"update_act", "update_chapter", "update_character"}
        for key in ("act_id", "chapter_id", "character_id"):
            if key in gap:
                by_id[(gap["fix_with"], gap[key])] = gap

    assert ("update_act", str(f["acts"][1].id)) in by_id, "第二幕没写 purpose，必须报出来"
    assert ("update_chapter", str(f["chapters"][3].id)) in by_id, "第四章没写 goal，必须报出来"
    assert ("update_character", str(f["characters"][1].id)) in by_id, "配角没写 motivation，必须报出来"

    # 第二章有正文也有蓝图 → 不再是缺口。「本章写成了什么」已删除（迁移 0022），
    # 「有正文但没写回摘要」不再是问题：下一章直接读结尾原文。
    assert ("update_chapter", str(f["chapters"][1].id)) not in by_id
    assert not any("写成了什么" in g["what"] for g in progress["design_gaps"])
    # 没有正文但有蓝图的章也不报缺口
    assert ("update_chapter", str(f["chapters"][2].id)) not in by_id


async def test_a_complete_design_has_no_gaps(db_session: AsyncSession, test_user: dict):
    project = await ProjectRepository(db_session).create("Healthy", "", test_user["id"])
    act = Act(project_id=project.id, name="第一幕", purpose="这一幕要干什么", sort_order=1)
    db_session.add(act)
    await db_session.flush()
    db_session.add_all([
        Chapter(project_id=project.id, act_id=act.id, title="第一章", goal="要写什么",
                content=BODY, status="final", sort_order=1),
        Chapter(project_id=project.id, act_id=act.id, title="第二章", goal="要写什么",
                content="", status="draft", sort_order=2),
        Character(project_id=project.id, name="主角", role="main",
                  motivation="想要什么", arc="从哪到哪", sort_order=1),
    ])
    await db_session.flush()

    progress = await ContextBuilder(db_session).build_progress(project.id)
    assert progress["design_gaps"] == []
    assert progress["content_break"]["global_order"] == 2


# ── 伏笔逾期 ──────────────────────────────────────────────────────────────────

async def test_overdue_setup_is_the_one_whose_pay_off_chapter_already_has_prose(db_session: AsyncSession, test_user: dict):
    f = await _progress_fixture(db_session, test_user)
    progress = await ContextBuilder(db_session).build_progress(f["project"].id)

    overdue = progress["overdue_setups"]
    assert [entry["id"] for entry in overdue] == [str(f["setups"]["旧硬币"].id)]
    entry = overdue[0]
    assert entry["status"] == "open"
    assert entry["planted_in_order"] == 1
    assert entry["pay_off_in_order"] == 2
    assert "第 2 章" in entry["why"]


async def test_open_setups_exclude_paid_and_include_unplanned(db_session: AsyncSession, test_user: dict):
    f = await _progress_fixture(db_session, test_user)
    progress = await ContextBuilder(db_session).build_progress(f["project"].id)

    open_ids = {entry["id"] for entry in progress["open_setups"]}
    assert open_ids == {str(f["setups"][name].id) for name in ("旧硬币", "钥匙", "一封信")}
    assert str(f["setups"]["刀"].id) not in open_ids, "已收的伏笔不算 open"

    unplanned = next(e for e in progress["open_setups"] if e["id"] == str(f["setups"]["一封信"].id))
    assert unplanned["pay_off_in_order"] is None, "还没定在哪收，不是逾期"
    assert str(f["setups"]["一封信"].id) not in {e["id"] for e in progress["overdue_setups"]}

    pending = next(e for e in progress["open_setups"] if e["id"] == str(f["setups"]["钥匙"].id))
    assert pending["pay_off_in_order"] == 3, "打算收的那一章还没写 → 不算逾期"


# ── 最近改动的章 ──────────────────────────────────────────────────────────────

async def test_recently_changed_chapters_shape(db_session: AsyncSession, test_user: dict):
    """最近改动的章由进度清单承担（旧 ``read_recent_chapters`` 已并入）。"""
    f = await _progress_fixture(db_session, test_user)
    progress = await ContextBuilder(db_session).build_progress(f["project"].id)

    recent = progress["recently_changed_chapters"]
    assert 0 < len(recent) <= 5
    for entry in recent:
        assert set(entry) == {"id", "global_order", "title", "status", "written", "updated_at"}
        assert "content" not in entry, "最近改动的章不该拖上正文"
    written = {e["global_order"] for e in recent if e["written"]}
    assert written <= {1, 2}


# ── 下一步 ────────────────────────────────────────────────────────────────────

async def test_next_step_is_the_first_design_gap(db_session: AsyncSession, test_user: dict):
    f = await _progress_fixture(db_session, test_user)
    progress = await ContextBuilder(db_session).build_progress(f["project"].id)

    assert progress["design_gaps"], "fixture 里一定有设计缺口"
    assert progress["next_step"]["kind"] == "补设计"
    assert progress["next_step"]["what"] == progress["design_gaps"][0]["what"]


async def test_next_step_prefers_the_content_break_when_design_is_complete(db_session: AsyncSession, test_user: dict):
    project = await ProjectRepository(db_session).create("Healthy", "", test_user["id"])
    act = Act(project_id=project.id, name="第一幕", purpose="要干什么", sort_order=1)
    db_session.add(act)
    await db_session.flush()
    db_session.add(Chapter(project_id=project.id, act_id=act.id, title="第一章",
                           goal="要写什么", content=BODY,
                           status="final", sort_order=1))
    await db_session.flush()

    progress = await ContextBuilder(db_session).build_progress(project.id)
    assert progress["design_gaps"] == []
    assert progress["content_break"] is None
    assert progress["next_step"]["kind"] == "完成"


async def test_next_step_is_the_overdue_setup_line_when_that_is_all_that_is_left(db_session: AsyncSession, test_user: dict):
    """设计齐、正文齐，只剩一条逾期伏笔 → 下一步就是收伏笔。"""
    project = await ProjectRepository(db_session).create("Overdue", "", test_user["id"])
    act = Act(project_id=project.id, name="第一幕", purpose="要干什么", sort_order=1)
    db_session.add(act)
    await db_session.flush()
    ch1 = Chapter(project_id=project.id, act_id=act.id, title="第一章", goal="要写什么",
                  content=BODY, status="final", sort_order=1)
    db_session.add(ch1)
    await db_session.flush()
    db_session.add(Setup(project_id=project.id, note="旧硬币", planted_in=ch1.id,
                         pay_off_in=ch1.id, status="open"))
    await db_session.flush()

    progress = await ContextBuilder(db_session).build_progress(project.id)
    assert progress["design_gaps"] == []
    assert progress["content_break"] is None
    assert progress["overdue_setups"]
    assert progress["next_step"]["kind"] == "收伏笔"


# ── 空项目 ────────────────────────────────────────────────────────────────────

async def test_empty_project_reports_nothing_missing(db_session: AsyncSession, test_user: dict):
    project = await ProjectRepository(db_session).create("Empty", "", test_user["id"])
    progress = await ContextBuilder(db_session).build_progress(project.id)

    assert progress["act_count"] == 0
    assert progress["chapter_count"] == 0
    assert progress["written_chapter_count"] == 0
    assert progress["content_break"] is None
    assert progress["design_gaps"] == []
    assert progress["open_setups"] == []
    assert progress["overdue_setups"] == []
    assert progress["next_step"]["kind"] == "完成"


# ── 工具层 ────────────────────────────────────────────────────────────────────

async def test_read_progress_tool_returns_the_same_clist_and_is_read_only(db_session: AsyncSession, test_user: dict):
    from app.agent.tools.read_tools import ReadProgressTool

    f = await _progress_fixture(db_session, test_user)
    result = await ReadProgressTool().run(
        db=db_session, project_id=str(f["project"].id), user_id=str(test_user["id"]))

    assert result.success, result.error
    expected = await ContextBuilder(db_session).build_progress(f["project"].id)
    assert result.data == expected
    assert not ReadProgressTool().is_write_operation


async def test_read_progress_requires_project_ownership(db_session: AsyncSession, test_user: dict):
    from app.agent.tools.read_tools import ReadProgressTool

    project = await ProjectRepository(db_session).create("Owned", "", test_user["id"])
    result = await ReadProgressTool().run(
        db=db_session, project_id=str(project.id), user_id=str(uuid.uuid4()))
    assert not result.success
