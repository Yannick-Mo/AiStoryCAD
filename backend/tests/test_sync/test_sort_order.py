"""顺序规则 —— ``sort_order`` 是位置的唯一真相。

章按**幕**编号（幕内 1..N），幕按项目编号；新行由服务端追加进自己的容器，
位置由服务端决定。客户端传进来的裸序号一律被忽略，也不会被工具层接受：
**移动只能用「插到谁之后 / 插到开头 / 接在末尾」表达**（规格第 5 节）。

场景层已删除：原来的「场按章编号」没有对应物了。移动语义与密集归一化的完整
覆盖在 ``test_sequence.py``。
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.project.repository import ProjectRepository
from app.storycad.models import Chapter
from app.storycad.repository import AiStoryCADRepository


pytestmark = pytest.mark.asyncio


async def _project(db_session: AsyncSession, test_user: dict, acts: int = 2):
    repo = AiStoryCADRepository(db_session)
    project = await ProjectRepository(db_session).create("Ordering", "", test_user["id"])
    act_ids = [str(uuid.uuid4()) for _ in range(acts)]
    await repo.sync_editor_data(project.id, {"acts": {"created": [
        {"id": act_id, "name": f"第 {i + 1} 幕", "purpose": f"第 {i + 1} 幕要干什么"}
        for i, act_id in enumerate(act_ids)
    ]}})
    return project, repo, act_ids


async def _add_chapter(repo: AiStoryCADRepository, project_id, act_id: str, title: str, **extra):
    chapter_id = str(uuid.uuid4())
    await repo.sync_editor_data(project_id, {"chapters": {"created": [
        {"id": chapter_id, "act_id": act_id, "title": title, **extra}
    ]}})
    return chapter_id


async def _titles_in_act(db_session: AsyncSession, act_id: str) -> list[str]:
    rows = (await db_session.execute(
        select(Chapter).where(Chapter.act_id == uuid.UUID(act_id))
    )).scalars().all()
    return [row.title for row in sorted(rows, key=lambda r: r.sort_order)]


async def test_new_chapter_appends_within_its_own_act(db_session: AsyncSession, test_user: dict):
    """Chapter numbering is per act, so act 2 does not continue act 1's count."""
    project, repo, (act1, act2) = await _project(db_session, test_user)
    for title in ("A1", "A2", "A3"):
        await _add_chapter(repo, project.id, act1, title)
    await _add_chapter(repo, project.id, act2, "B1")

    data = await repo.get_editor_data(project.id)
    assert {c["title"]: c["sort_order"] for c in data["chapters"]} == {
        "A1": 1, "A2": 2, "A3": 3, "B1": 1,
    }


async def test_client_supplied_sort_order_is_ignored_on_create(db_session: AsyncSession, test_user: dict):
    """A client cannot inject a position: the server appends inside the act."""
    project, repo, (act1,) = await _project(db_session, test_user, acts=1)
    await repo.sync_editor_data(project.id, {"chapters": {"created": [
        {"id": str(uuid.uuid4()), "act_id": act1, "title": "first", "sort_order": 7},
        {"id": str(uuid.uuid4()), "act_id": act1, "title": "second", "sort_order": 7},
    ]}})

    data = await repo.get_editor_data(project.id)
    assert [c["sort_order"] for c in data["chapters"]] == [1, 2]


async def test_client_supplied_act_sort_order_is_ignored(db_session: AsyncSession, test_user: dict):
    repo = AiStoryCADRepository(db_session)
    project = await ProjectRepository(db_session).create("Acts", "", test_user["id"])
    await repo.sync_editor_data(project.id, {"acts": {"created": [
        {"id": str(uuid.uuid4()), "name": "第一幕", "purpose": "甲", "sort_order": 9},
        {"id": str(uuid.uuid4()), "name": "第二幕", "purpose": "乙", "sort_order": 9},
    ]}})

    data = await repo.get_editor_data(project.id)
    assert [a["sort_order"] for a in data["acts"]] == [1, 2]


async def test_editor_data_returns_chapters_in_narrative_order(db_session: AsyncSession, test_user: dict):
    """Payload order is act order then in-act order, not creation order."""
    project, repo, (act1, act2) = await _project(db_session, test_user)
    await _add_chapter(repo, project.id, act1, "A1")
    await _add_chapter(repo, project.id, act2, "B1")
    await _add_chapter(repo, project.id, act1, "A2")

    data = await repo.get_editor_data(project.id)
    assert [c["title"] for c in data["chapters"]] == ["A1", "A2", "B1"]


async def test_editor_data_never_carries_the_body(db_session: AsyncSession, test_user: dict):
    """编辑器全量加载不带正文：``chapters.content`` 是延迟列。"""
    project, repo, (act1,) = await _project(db_session, test_user, acts=1)
    await _add_chapter(repo, project.id, act1, "A1")

    data = await repo.get_editor_data(project.id)
    assert "content" not in data["chapters"][0]
    # 派生统计也不再落库
    for gone in ("word_count", "scene_count", "total_words"):
        assert gone not in data["chapters"][0], gone


async def test_update_chapter_does_not_take_a_raw_sort_order(db_session: AsyncSession, test_user: dict):
    """位置不在 update_chapter 里改：裸序号已从工具层彻底移除。"""
    from app.agent.tools.structure_tools import UpdateChapterTool

    props = UpdateChapterTool.meta.parameters["properties"]
    assert "sort_order" not in props
    assert set(props) == {"chapter_id", "title", "goal", "status"}

    project, repo, (act1,) = await _project(db_session, test_user, acts=1)
    first = await _add_chapter(repo, project.id, act1, "first")
    second = await _add_chapter(repo, project.id, act1, "second")

    result = await UpdateChapterTool().run(
        db=db_session, project_id=str(project.id), user_id=str(test_user["id"]),
        chapter_id=second, sort_order=1,
    )
    assert result.success, result.error
    assert "sort_order" not in result.data
    assert await _titles_in_act(db_session, act1) == ["first", "second"], \
        "update_chapter 不该改动位置"
    assert first


async def test_move_chapter_places_chapter_after_target(db_session: AsyncSession, test_user: dict):
    """move_chapter 只接受「插到谁之后」，序号由实现归一化。"""
    from app.agent.tools.structure_tools import MoveChapterTool

    project, repo, (act1,) = await _project(db_session, test_user, acts=1)
    a = await _add_chapter(repo, project.id, act1, "A")
    b = await _add_chapter(repo, project.id, act1, "B")
    c = await _add_chapter(repo, project.id, act1, "C")

    result = await MoveChapterTool().run(
        db=db_session, project_id=str(project.id), user_id=str(test_user["id"]),
        chapter_id=c, after_id=a,
    )
    assert result.success, result.error
    assert await _titles_in_act(db_session, act1) == ["A", "C", "B"]
    assert result.data["sort_order"] == 2
    assert result.data["global_order"] == 2
    assert b


async def test_move_chapter_to_another_act_changes_ownership(db_session: AsyncSession, test_user: dict):
    """章能换幕——归属是可以改的（旧版里这是被拒绝的）。"""
    from app.agent.tools.structure_tools import MoveChapterTool

    project, repo, (act1, act2) = await _project(db_session, test_user)
    a = await _add_chapter(repo, project.id, act1, "A")
    await _add_chapter(repo, project.id, act1, "B")
    await _add_chapter(repo, project.id, act2, "X")

    result = await MoveChapterTool().run(
        db=db_session, project_id=str(project.id), user_id=str(test_user["id"]),
        chapter_id=a, act_id=act2,
    )
    assert result.success, result.error
    assert await _titles_in_act(db_session, act1) == ["B"]
    assert await _titles_in_act(db_session, act2) == ["X", "A"]


async def test_move_chapter_rejects_after_id_from_another_act(db_session: AsyncSession, test_user: dict):
    """after_id 必须在目标幕里，否则插到哪儿是不确定的。"""
    from app.agent.tools.structure_tools import MoveChapterTool

    project, repo, (act1, act2) = await _project(db_session, test_user)
    a = await _add_chapter(repo, project.id, act1, "A")
    other = await _add_chapter(repo, project.id, act2, "B")

    result = await MoveChapterTool().run(
        db=db_session, project_id=str(project.id), user_id=str(test_user["id"]),
        chapter_id=a, after_id=other,
    )
    assert not result.success
    assert "不在目标容器" in (result.error or "")
    assert result.correction_hint
