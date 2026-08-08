import uuid
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.config import settings
from app.project.models import Base
from app.user.models import User


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        yield session
        await session.close()
        await conn.rollback()
    # 遗留表（v2 scene_fact_cache）与 v3 ledger 表不在 Base.metadata 注册表
    # 集合里（被测模块按需 import），drop_all 不会清理它们，而它们的 FK
    # 指向 scenes → 必须在 drop_all 之前 CASCADE 清理，否则 DROP TABLE scenes
    # 触发 DependentObjectsStillExistError。
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


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> dict:
    from app.user.repository import UserRepository
    from app.user.service import UserService
    repo = UserRepository(db_session)
    user = await repo.create("testuser", "test@example.com", await UserService._hash_password("password"))
    return {"id": user.id, "username": user.username}
