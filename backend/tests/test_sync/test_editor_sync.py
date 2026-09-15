"""Tests for the editor data sync pipeline — the most critical data flow.

同步的返回值变了：不再有 ``version``（那是 ``project_versions`` 的编辑器同步游标，
既不是版本也不能回滚，已随该表删除）。现在返回 ``{ok, applied, renumbered}`` ——
顺序的归一化在同一次调用里完成，``renumbered`` 如实报出改了多少行。
"""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.storycad.repository import AiStoryCADRepository
from app.project.repository import ProjectRepository


pytestmark = pytest.mark.asyncio


async def test_sync_creates_act(db_session: AsyncSession, test_user: dict):
    """Creating an act via sync should persist it and report what it applied."""
    repo = AiStoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)

    project = await project_repo.create("Test Project", "", test_user["id"])

    act_id = str(uuid.uuid4())
    payload = {
        "acts": {
            "created": [{"id": act_id, "name": "第一幕", "purpose": "把他扣在镇上",
                         "color": "#f97316"}]
        }
    }
    result = await repo.sync_editor_data(project.id, payload)

    assert result["applied"] >= 1, result
    assert "version" not in result, "编辑器同步游标已随 project_versions 删除"

    data = await repo.get_editor_data(project.id)
    acts = data["acts"]
    assert len(acts) == 1
    assert acts[0]["name"] == "第一幕"
    assert acts[0]["purpose"] == "把他扣在镇上"
    assert acts[0]["sort_order"] == 1


async def test_sync_of_nothing_reports_zero(db_session: AsyncSession, test_user: dict):
    repo = AiStoryCADRepository(db_session)
    project = await ProjectRepository(db_session).create("Empty", "", test_user["id"])

    assert await repo.sync_editor_data(project.id, {"acts": {}}) == {
        "applied": 0, "renumbered": {},
    }


async def test_sync_creates_chapter_under_act(db_session: AsyncSession, test_user: dict):
    """Chapters should link to acts correctly through sync."""
    repo = AiStoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)

    project = await project_repo.create("Test Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())

    payload = {
        "acts": {"created": [{"id": act_id, "name": "第一幕", "purpose": "甲"}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id,
                                  "title": "第一章", "goal": "本章要写什么"}]},
    }
    await repo.sync_editor_data(project.id, payload)

    data = await repo.get_editor_data(project.id)
    chapters = data["chapters"]
    assert len(chapters) == 1
    assert chapters[0]["act_id"] == act_id
    assert chapters[0]["title"] == "第一章"
    assert chapters[0]["goal"] == "本章要写什么"
    assert chapters[0]["status"] == "draft"


async def test_sync_deletes_act_cascades_chapters(db_session: AsyncSession, test_user: dict):
    """Deleting an act should cascade-delete its chapters (CASCADE FK)."""
    repo = AiStoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)

    project = await project_repo.create("Test Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())

    await repo.sync_editor_data(project.id, {
        "acts": {"created": [{"id": act_id, "name": "第一幕", "purpose": "甲"}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "第一章"}]},
    })

    await repo.sync_editor_data(project.id, {"acts": {"deleted": [act_id]}})

    data = await repo.get_editor_data(project.id)
    assert len(data["acts"]) == 0
    assert len(data["chapters"]) == 0, "Chapters should be cascade-deleted with the act"


async def test_deleting_an_act_renumbers_the_remaining_acts(db_session: AsyncSession, test_user: dict):
    repo = AiStoryCADRepository(db_session)
    project = await ProjectRepository(db_session).create("Acts", "", test_user["id"])
    ids = [str(uuid.uuid4()) for _ in range(3)]
    await repo.sync_editor_data(project.id, {"acts": {"created": [
        {"id": act_id, "name": f"第{i + 1}幕", "purpose": "甲"} for i, act_id in enumerate(ids)
    ]}})

    result = await repo.sync_editor_data(project.id, {"acts": {"deleted": [ids[1]]}})

    data = await repo.get_editor_data(project.id)
    assert [a["sort_order"] for a in data["acts"]] == [1, 2]
    assert [a["name"] for a in data["acts"]] == ["第1幕", "第3幕"]
    assert result["renumbered"].get("acts", 0) >= 1, "删掉中间一幕必须把序号收回密集"


async def test_sync_updates_global_settings(db_session: AsyncSession, test_user: dict):
    """Saving global settings should persist to the project row."""
    repo = AiStoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)

    project = await project_repo.create("Test Project", "", test_user["id"])

    payload = {
        "projects": {"updated": [{"id": str(project.id), "global_settings": "一个奇幻世界"}]}
    }
    result = await repo.sync_editor_data(project.id, payload)

    assert result["applied"] >= 1
    data = await repo.get_editor_data(project.id)
    assert data["global_settings"] == "一个奇幻世界"


async def test_body_text_round_trips_through_the_content_accessor(
    db_session: AsyncSession, test_user: dict
):
    """正文落库的唯一路径是 ``save_chapter_content``（``PUT /chapters/{id}/content``）；
    字数不再有列，永远实时算，而且列表载荷永远不带正文。"""
    repo = AiStoryCADRepository(db_session)
    project = await ProjectRepository(db_session).create("Content", "", test_user["id"])
    act_id, chapter_id = str(uuid.uuid4()), str(uuid.uuid4())
    await repo.sync_editor_data(project.id, {
        "acts": {"created": [{"id": act_id, "name": "第一幕", "purpose": "甲"}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "第一章"}]},
    })

    body = "陈默推门进去。" * 40
    await repo.save_chapter_content(uuid.UUID(chapter_id), project.id, body)

    assert await repo.get_chapter_content(uuid.UUID(chapter_id), project.id) == body

    from app.agent.utils import count_words
    # 6 个汉字 × 40 + 结尾那一串标点算 1 个非中文词
    assert count_words(body) == 6 * 40 + 1, "字数由正文实时算出，不落库"

    data = await repo.get_editor_data(project.id)
    assert "content" not in data["chapters"][0], "列表载荷不带正文"
    assert "word_count" not in data["chapters"][0], "派生列已删除"


async def test_editor_sync_can_persist_chapter_content(
    db_session: AsyncSession, test_user: dict
):
    """``chapters.updated[{content}]`` 是编辑器整体保存正文要走的路。

    现状：``AiStoryCADRepository.update_entity``（storycad/repository.py:275）
    对每一个键做 ``hasattr(obj, key)``，而 ``chapters.content`` 是延迟列 ——
    ``hasattr`` 正好触发懒加载并抛 ``InvalidRequestError``，于是整条同步请求 500。
    """
    repo = AiStoryCADRepository(db_session)
    project = await ProjectRepository(db_session).create("Content", "", test_user["id"])
    act_id, chapter_id = str(uuid.uuid4()), str(uuid.uuid4())
    await repo.sync_editor_data(project.id, {
        "acts": {"created": [{"id": act_id, "name": "第一幕", "purpose": "甲"}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "第一章"}]},
    })

    body = "陈默推门进去。" * 40
    await repo.sync_editor_data(project.id, {
        "chapters": {"updated": [{"id": chapter_id, "content": body}]},
    })

    assert await repo.get_chapter_content(uuid.UUID(chapter_id), project.id) == body
