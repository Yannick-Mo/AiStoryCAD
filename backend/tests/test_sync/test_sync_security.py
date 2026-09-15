"""Tests that sync operations scope entity lookups to the owning project,
and that create_entity cannot be abused to inject server-managed fields.

场景层已删除：fixture 只建 幕 / 章。派生统计列（word_count / scene_count /
total_words）也不再存在于 chapters 上——字数实时算，所以保护名单回到纯粹的
「服务端管理字段」（id / project_id / created_at / updated_at）。
"""
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storycad.repository import (
    AiStoryCADRepository,
    CREATE_PROTECTED,
    PROTECTED_FIELDS,
    with_chapter_content,
)
from app.project.repository import ProjectRepository
from app.storycad.models import Act, Chapter


# asyncio_mode=auto（pytest.ini）：异步用例自动识别，无需模块级标记
# —— 本文件里还有同步用例，加了标记只会换来一条 PytestWarning。


async def _create_own_project(
    db_session: AsyncSession, test_user: dict,
) -> tuple[AiStoryCADRepository, object, str, str]:
    """Create a project with one act/chapter; return (repo, project, act_id, chapter_id)."""
    repo = AiStoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)
    project = await project_repo.create("OWN Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())
    payload = {
        "acts": {"created": [{"id": act_id, "name": "OWN Act", "purpose": "甲"}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "OWN Chapter"}]},
    }
    await repo.sync_editor_data(project.id, payload)
    return repo, project, act_id, chapter_id


async def _create_foreign_project(
    db_session: AsyncSession, test_user: dict,
) -> tuple[AiStoryCADRepository, object, str, str]:
    """Create a second project with one act/chapter."""
    repo = AiStoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)
    project = await project_repo.create("FOREIGN Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    chapter_id = str(uuid.uuid4())
    payload = {
        "acts": {"created": [{"id": act_id, "name": "FOREIGN Act", "purpose": "甲"}]},
        "chapters": {"created": [{"id": chapter_id, "act_id": act_id, "title": "FOREIGN Chapter"}]},
    }
    await repo.sync_editor_data(project.id, payload)
    return repo, project, act_id, chapter_id


async def _create_simple_project(
    db_session: AsyncSession, test_user: dict,
) -> tuple[AiStoryCADRepository, object, str]:
    """Minimal project with one act (no chapter) for field-filter tests."""
    repo = AiStoryCADRepository(db_session)
    project_repo = ProjectRepository(db_session)
    project = await project_repo.create("Test Project", "", test_user["id"])
    act_id = str(uuid.uuid4())
    await repo.sync_editor_data(project.id, {
        "acts": {"created": [{"id": act_id, "name": "Test Act", "purpose": "甲"}]},
    })
    return repo, project, act_id


# =========================================================================
# Fix A: sync_editor_data scopes update/delete to the owning project
# =========================================================================


async def test_sync_update_skips_entity_from_other_project(
    db_session: AsyncSession, test_user: dict,
):
    """Updating an entity that belongs to a different project must be silently skipped."""
    repo_own, own_project, _, own_chapter_id = await _create_own_project(db_session, test_user)
    _, foreign_project, _, _ = await _create_foreign_project(db_session, test_user)

    # Attempt to modify the OWN project's chapter via a sync on the FOREIGN project
    await repo_own.sync_editor_data(foreign_project.id, {
        "chapters": {"updated": [{"id": own_chapter_id, "title": "HACKED"}]},
    })

    fetched_own = await repo_own.get_entity(Chapter, uuid.UUID(own_chapter_id))
    assert fetched_own["title"] == "OWN Chapter", "Entity from other project must not be updated"
    assert own_project.id != foreign_project.id


async def test_sync_update_skips_nonexistent_entity(
    db_session: AsyncSession, test_user: dict,
):
    """Updating a non-existent entity id must be silently skipped (no crash)."""
    repo_own, own_project, _, _ = await _create_own_project(db_session, test_user)

    # Must not raise; must not modify anything
    await repo_own.sync_editor_data(own_project.id, {
        "chapters": {"updated": [{"id": str(uuid.uuid4()), "title": "NOWHERE"}]},
    })


async def test_sync_delete_skips_entity_from_other_project(
    db_session: AsyncSession, test_user: dict,
):
    """Deleting an entity that belongs to a different project must be silently skipped."""
    repo_own, own_project, _, own_chapter_id = await _create_own_project(db_session, test_user)
    _, foreign_project, _, _ = await _create_foreign_project(db_session, test_user)

    await repo_own.sync_editor_data(foreign_project.id, {
        "chapters": {"deleted": [own_chapter_id]},
    })

    fetched = await repo_own.get_entity(Chapter, uuid.UUID(own_chapter_id))
    assert fetched is not None, "Entity from other project must not be deleted"
    assert own_project.id != foreign_project.id


async def test_sync_delete_skips_nonexistent_entity(
    db_session: AsyncSession, test_user: dict,
):
    """Deleting a non-existent entity id must be silently skipped (no crash)."""
    repo_own, own_project, _, _ = await _create_own_project(db_session, test_user)
    await repo_own.sync_editor_data(own_project.id, {"acts": {"deleted": [str(uuid.uuid4())]}})


async def test_sync_update_within_own_project_still_works(
    db_session: AsyncSession, test_user: dict,
):
    """Updating an entity within the same project must still succeed normally."""
    repo_own, own_project, _, own_chapter_id = await _create_own_project(db_session, test_user)

    result = await repo_own.sync_editor_data(own_project.id, {
        "chapters": {"updated": [{"id": own_chapter_id, "title": "Updated Chapter"}]},
    })
    assert result["applied"] >= 1, "sync within own project must succeed"

    fetched = await repo_own.get_entity(Chapter, uuid.UUID(own_chapter_id))
    assert fetched["title"] == "Updated Chapter"


async def test_sync_delete_within_own_project_still_works(
    db_session: AsyncSession, test_user: dict,
):
    """Deleting an entity within the same project must still succeed normally."""
    repo_own, own_project, _, own_chapter_id = await _create_own_project(db_session, test_user)

    result = await repo_own.sync_editor_data(own_project.id, {
        "chapters": {"deleted": [own_chapter_id]},
    })
    assert result["applied"] >= 1, "sync within own project must succeed"

    fetched = await repo_own.get_entity(Chapter, uuid.UUID(own_chapter_id))
    assert fetched is None, "Entity must be deleted"


# =========================================================================
# Fix B: create_entity filters protected fields
# =========================================================================


async def test_create_entity_ignores_created_at(
    db_session: AsyncSession, test_user: dict,
):
    """created_at must be stripped from create_entity data."""
    repo, project, _ = await _create_simple_project(db_session, test_user)

    forged = "2000-01-01T00:00:00+00:00"
    act = await repo.create_entity(Act, {
        "id": str(uuid.uuid4()),
        "project_id": str(project.id),
        "name": "Test Act",
        "sort_order": 0,
        "created_at": forged,
    })
    assert act["created_at"] != forged, "created_at must not be client-settable"


async def test_create_entity_drops_derived_and_unknown_fields(
    db_session: AsyncSession, test_user: dict,
):
    """派生值不再有列，所以「传了也不落库」——而且不该因此报错。"""
    repo, project, act_id = await _create_simple_project(db_session, test_user)

    chapter = await repo.create_entity(Chapter, {
        "id": str(uuid.uuid4()),
        "project_id": str(project.id),
        "act_id": act_id,
        "title": "Test Chapter",
        "sort_order": 1,
        "word_count": 99999,
        "scene_count": 42,
        "total_words": 123456,
    })
    for gone in ("word_count", "scene_count", "total_words"):
        assert gone not in chapter, f"{gone} 不是列，不该出现在载荷里"
    assert chapter["title"] == "Test Chapter"
    assert not hasattr(Chapter, "word_count")
    assert not hasattr(Chapter, "scene_count")


async def test_create_entity_can_carry_chapter_content_but_never_returns_it(
    db_session: AsyncSession, test_user: dict,
):
    """建章时可以带正文，但返回的载荷里没有正文（延迟列）。"""
    repo, project, act_id = await _create_simple_project(db_session, test_user)
    chapter_id = uuid.uuid4()

    row = await repo.create_entity(Chapter, {
        "id": str(chapter_id),
        "project_id": str(project.id),
        "act_id": act_id,
        "title": "带正文的章",
        "content": "陈默推门进去。",
        "sort_order": 1,
    })
    assert row["title"] == "带正文的章"
    assert "content" not in row

    fresh = (await db_session.execute(
        with_chapter_content(select(Chapter).where(Chapter.id == chapter_id))
    )).scalars().one()
    assert fresh.content == "陈默推门进去。"


async def test_create_entity_allows_id_and_project_id(
    db_session: AsyncSession, test_user: dict,
):
    """id and project_id are legitimate at creation time even though they are protected on update."""
    repo, project, _ = await _create_simple_project(db_session, test_user)

    expected_id = str(uuid.uuid4())
    act = await repo.create_entity(Act, {
        "id": expected_id,
        "project_id": str(project.id),
        "name": "Legitimate Act",
        "sort_order": 0,
    })
    assert act["id"] == expected_id, "id must be settable on create"
    assert act["project_id"] == str(project.id), "project_id must be settable on create"


def test_create_protected_constant_definition():
    """保护名单就是「服务端管理字段」本身——派生值不再有列，所以不在名单里。"""
    # sort_order 也在里面：移动只能用「插到谁之后」表达，序号由实现归一化，
    # 客户端自己算序号必然算错（历史上就是这么撞出重复序号的）。
    assert PROTECTED_FIELDS == {"id", "project_id", "created_at", "updated_at", "sort_order"}
    assert "id" not in CREATE_PROTECTED
    assert "project_id" not in CREATE_PROTECTED
    assert "created_at" in CREATE_PROTECTED
    assert "updated_at" in CREATE_PROTECTED
    # sort_order 在**创建**时也放行：两条创建路径都先调 assign_initial_sort_order
    # 把服务端算好的序号写进 data，再由 create_entity 落库；在这里过滤掉的话，
    # 新行会全落到模型默认值 0 上并列成一团。客户端传的值仍无效——会被服务端覆盖。
    assert "sort_order" not in CREATE_PROTECTED
    assert CREATE_PROTECTED == PROTECTED_FIELDS - {"id", "project_id", "sort_order"}
