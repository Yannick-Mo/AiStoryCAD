import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.project.models import Base

# Register all model tables on Base.metadata so create_all covers them.
import app.project.models  # noqa: F401
import app.storycad.models  # noqa: F401
import app.knowledge.models  # noqa: F401
import app.settings.models  # noqa: F401
import app.agent.memory.models  # noqa: F401
import app.agent.consistency.orm  # noqa: F401


def _test_database_url() -> str:
    override = os.getenv("TEST_DATABASE_URL")
    if override:
        return override
    from app.config import settings

    return settings.database_url.rsplit("/", 1)[0] + "/storyforge_test"


@pytest_asyncio.fixture
async def client(monkeypatch):
    """FastAPI app wired to an isolated test database, with the per-user
    rate limiter neutralized so endpoint tests exercise behaviour, not limits."""
    import app.main as main_module
    from app.api import deps
    from app.api.rate_limiter import rate_limiter

    app = main_module.app
    url = _test_database_url()
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with engine.connect() as conn:
            session = AsyncSession(bind=conn, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[deps.get_db] = override_get_db

    async def _always_ok(*args, **kwargs):
        return True

    monkeypatch.setattr(rate_limiter, "check", _always_ok)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.pop(deps.get_db, None)
    # Mirror tests/conftest teardown: drop unregistered v2/v3 tables with
    # CASCADE before Base tables, otherwise DROP TABLE scenes hits FK errors.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DROP TABLE IF EXISTS scene_fact_cache, consistency_facts,"
                " entity_aliases, conflict_candidates, consistency_time_cache,"
                " consistency_fact_queue, consistency_logs, consistency_reports"
                " CASCADE"
            )
        )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
