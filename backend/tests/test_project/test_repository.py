import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.project.repository import ProjectRepository
from app.project.service import ProjectService


@pytest.mark.asyncio
async def test_create_project(db_session: AsyncSession, test_user: dict):
    repo = ProjectRepository(db_session)
    project = await repo.create("Test Project", "", test_user["id"])
    assert project.title == "Test Project"
    assert project.status == "init"
    assert project.id is not None
    assert project.owner_id == test_user["id"]


@pytest.mark.asyncio
async def test_get_project(db_session: AsyncSession, test_user: dict):
    repo = ProjectRepository(db_session)
    created = await repo.create("Get Test", "", test_user["id"])
    found = await repo.get(created.id)
    assert found is not None
    assert found.title == "Get Test"
    assert found.owner_id == test_user["id"]


@pytest.mark.asyncio
async def test_project_api_payload_does_not_expose_deprecated_ending(
    db_session: AsyncSession, test_user: dict
):
    """``ending`` 列保留在库里，但作品层接口不再返回它（旧项目内容不动）。"""
    service = ProjectService(db_session)
    created = await service.create_project("No Ending", "", test_user["id"])

    project = await ProjectRepository(db_session).get(uuid.UUID(created["id"]))
    project.ending = "旧终局"
    await db_session.flush()

    got = await service.get_project(project.id, test_user["id"])
    assert got is not None
    assert "ending" not in got

    listed = await service.list_projects(test_user["id"])
    assert listed["items"]
    assert all("ending" not in item for item in listed["items"])
