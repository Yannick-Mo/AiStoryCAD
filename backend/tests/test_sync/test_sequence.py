"""顺序规范（``app/storycad/sequence.py``）—— 顺序只有一个真相，移动只有三种说法。

场景层和 timeline 投影都已删除：顺序不再是"投影出来的"，而是**维护出来的**。

规格第 5 节的四条在这里落地：

1. 位置的真相只有一份：层内序号 + 父子归属（全局章号是算出来的，不存）；
2. 归属可改（章能换幕）；
3. 移动只用「插到谁之后 / 插到开头 / 接在末尾」表达，工具层永不暴露裸序号；
4. 同级 ``sort_order`` 由实现归一化成连续 1..N —— 这是实现的责任。

本文件取代了 ``test_timeline_chain.py``：那条主时序线（``ChapterEdge``）整张表
已删除，需要在移动之后归一化，而不是把顺序再存一份投影。
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.project.repository import ProjectRepository
from app.storycad.models import Act, Chapter
from app.storycad.sequence import move_item, ordered_chapter_ids, renumber_container


pytestmark = pytest.mark.asyncio


async def _project(db_session: AsyncSession, test_user: dict, acts: int = 2, per_act: int = 3):
    """建一个 acts 幕 × per_act 章的项目，返回 (project, act_ids, chapters_by_act)。"""
    project = await ProjectRepository(db_session).create("Sequence", "", test_user["id"])
    act_ids = [uuid.uuid4() for _ in range(acts)]
    for i, act_id in enumerate(act_ids):
        db_session.add(Act(id=act_id, project_id=project.id, name=f"第{i + 1}幕",
                           purpose=f"第{i + 1}幕要干什么", sort_order=i + 1))

    chapters: dict[uuid.UUID, list[Chapter]] = {}
    for index, act_id in enumerate(act_ids):
        rows = []
        for j in range(per_act):
            chapter = Chapter(id=uuid.uuid4(), project_id=project.id, act_id=act_id,
                              title=f"A{index + 1}C{j + 1}", goal="本章要写什么",
                              sort_order=j + 1)
            db_session.add(chapter)
            rows.append(chapter)
        chapters[act_id] = rows
    await db_session.flush()
    return project, act_ids, chapters


async def _in_act(db_session: AsyncSession, act_id) -> list[tuple[str, int]]:
    rows = (await db_session.execute(
        select(Chapter).where(Chapter.act_id == act_id)
    )).scalars().all()
    return [(row.title, row.sort_order) for row in sorted(rows, key=lambda r: r.sort_order)]


# ── 移动的三种说法 ────────────────────────────────────────────────────────────

async def test_after_id_inserts_directly_after_that_sibling(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user, acts=1)
    act1 = act_ids[0]
    c1, c2, c3 = chapters[act1]

    await move_item(db_session, Chapter, project.id, item_id=c3.id,
                    after_id=c1.id, container_column="act_id", container_id=act1)

    assert await _in_act(db_session, act1) == [("A1C1", 1), ("A1C3", 2), ("A1C2", 3)]
    assert (c2.title, c3.title) == ("A1C2", "A1C3")  # sanity: fixtures are distinct


async def test_position_start_prepends(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user, acts=1)
    act1 = act_ids[0]
    _c1, _c2, c3 = chapters[act1]

    await move_item(db_session, Chapter, project.id, item_id=c3.id,
                    position="start", container_column="act_id", container_id=act1)

    assert await _in_act(db_session, act1) == [("A1C3", 1), ("A1C1", 2), ("A1C2", 3)]


async def test_default_position_appends(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user, acts=1)
    act1 = act_ids[0]
    c1 = chapters[act1][0]

    await move_item(db_session, Chapter, project.id, item_id=c1.id,
                    container_column="act_id", container_id=act1)

    assert await _in_act(db_session, act1) == [("A1C2", 1), ("A1C3", 2), ("A1C1", 3)]


async def test_move_item_returns_the_row_with_its_new_sort_order(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user, acts=1)
    act1 = act_ids[0]
    _c1, _c2, c3 = chapters[act1]

    moved = await move_item(db_session, Chapter, project.id, item_id=c3.id,
                            position="start", container_column="act_id", container_id=act1)
    assert moved.id == c3.id
    assert moved.sort_order == 1


# ── 换幕：目标幕和原幕都要归一化 ─────────────────────────────────────────────

async def test_moving_a_chapter_to_another_act_renumbers_both_acts(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user)
    act1, act2 = act_ids
    c1, c2, c3 = chapters[act1]
    d1, _d2, _d3 = chapters[act2]

    await move_item(db_session, Chapter, project.id, item_id=c2.id,
                    after_id=d1.id, container_column="act_id", container_id=act2)

    # 目标幕：新章插在 D1 之后，序号密集到 1..4
    assert await _in_act(db_session, act2) == [("A2C1", 1), ("A1C2", 2), ("A2C2", 3), ("A2C3", 4)]
    # 原幕少了一行，必须归一化，不能留下空位
    assert await _in_act(db_session, act1) == [("A1C1", 1), ("A1C3", 2)]
    assert c2.act_id == act2
    assert (c1.title, c3.title) == ("A1C1", "A1C3")


async def test_moving_a_chapter_to_the_end_of_another_act_renumbers_both(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user)
    act1, act2 = act_ids
    c2 = chapters[act1][1]

    await move_item(db_session, Chapter, project.id, item_id=c2.id,
                    container_column="act_id", container_id=act2)

    assert await _in_act(db_session, act2) == [("A2C1", 1), ("A2C2", 2), ("A2C3", 3), ("A1C2", 4)]
    assert await _in_act(db_session, act1) == [("A1C1", 1), ("A1C3", 2)]


async def test_moving_the_only_chapter_empties_the_old_act(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user, acts=2, per_act=1)
    act1, act2 = act_ids
    only = chapters[act1][0]

    await move_item(db_session, Chapter, project.id, item_id=only.id,
                    container_column="act_id", container_id=act2)

    assert await _in_act(db_session, act1) == []
    assert await _in_act(db_session, act2) == [("A2C1", 1), ("A1C1", 2)]


# ── after_id 必须在目标容器里 ─────────────────────────────────────────────────

async def test_after_id_outside_the_target_container_raises(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user)
    act1, act2 = act_ids
    outsider = chapters[act1][0]      # 在 act1 里，不在 act2
    moving = chapters[act2][0]

    with pytest.raises(LookupError):
        await move_item(db_session, Chapter, project.id, item_id=moving.id,
                        after_id=outsider.id,
                        container_column="act_id", container_id=act2)


async def test_after_id_of_a_chapter_in_another_act_is_rejected_within_one_act(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user)
    act1, act2 = act_ids
    a = chapters[act1][0]
    b = chapters[act2][0]

    with pytest.raises(LookupError):
        await move_item(db_session, Chapter, project.id, item_id=a.id,
                        after_id=b.id,
                        container_column="act_id", container_id=act1)


async def test_unknown_item_raises(db_session: AsyncSession, test_user: dict):
    project, _act_ids, _chapters = await _project(db_session, test_user)
    with pytest.raises(LookupError):
        await move_item(db_session, Chapter, project.id, item_id=uuid.uuid4())


async def test_move_tool_reports_the_error_instead_of_raising(db_session: AsyncSession, test_user: dict):
    """工具层把 LookupError 转成可自纠的错误，而不是抛给主循环。"""
    from app.agent.tools.structure_tools import MoveChapterTool

    project, act_ids, chapters = await _project(db_session, test_user)
    act1, act2 = act_ids
    outsider = chapters[act1][0]
    moving = chapters[act2][0]

    result = await MoveChapterTool().run(
        db=db_session, project_id=str(project.id), user_id=str(test_user["id"]),
        chapter_id=str(moving.id), act_id=str(act2), after_id=str(outsider.id),
    )
    assert not result.success
    assert result.correction_hint


# ── 归一化与全局章号 ──────────────────────────────────────────────────────────

async def test_renumber_container_makes_siblings_dense(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user, acts=1)
    act1 = act_ids[0]
    # 序号留了空位（1 / 5 / 40），但相对顺序不变
    chapters[act1][1].sort_order = 5
    chapters[act1][2].sort_order = 40
    await db_session.flush()

    changed = await renumber_container(db_session, Chapter, project.id, "act_id", act1)
    assert changed == 2, "只有第 2、3 章的序号需要改"
    assert await _in_act(db_session, act1) == [("A1C1", 1), ("A1C2", 2), ("A1C3", 3)]
    # 已经是密集的：再调一次不改任何行
    assert await renumber_container(db_session, Chapter, project.id, "act_id", act1) == 0


async def test_ordered_chapter_ids_is_act_order_then_in_act_order(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user)
    act1, act2 = act_ids

    ordered = await ordered_chapter_ids(db_session, project.id)
    assert ordered == [c.id for c in chapters[act1]] + [c.id for c in chapters[act2]]


async def test_deleting_a_chapter_then_renumbering_leaves_no_gap(db_session: AsyncSession, test_user: dict):
    project, act_ids, chapters = await _project(db_session, test_user, acts=1)
    act1 = act_ids[0]
    _c1, c2, _c3 = chapters[act1]

    await db_session.delete(c2)
    await db_session.flush()
    await renumber_container(db_session, Chapter, project.id, "act_id", act1)

    assert await _in_act(db_session, act1) == [("A1C1", 1), ("A1C3", 2)]


async def test_editor_sync_renumbers_chapters_densely(db_session: AsyncSession, test_user: dict):
    """编辑器同步路径也走归一化：客户端各自算 max+1 会撞出重复序号。"""
    from app.storycad.repository import AiStoryCADRepository

    repo = AiStoryCADRepository(db_session)
    project, act_ids, _chapters = await _project(db_session, test_user, acts=1)
    act1 = act_ids[0]

    extra = uuid.uuid4()
    result = await repo.sync_editor_data(project.id, {"chapters": {"created": [
        {"id": str(extra), "act_id": str(act1), "title": "新增章", "sort_order": 99},
    ]}})

    assert result["applied"] >= 1
    assert await _in_act(db_session, act1) == [
        ("A1C1", 1), ("A1C2", 2), ("A1C3", 3), ("新增章", 4),
    ]
