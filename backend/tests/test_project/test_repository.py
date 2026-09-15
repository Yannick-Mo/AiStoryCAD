import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.project.repository import ProjectRepository


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
