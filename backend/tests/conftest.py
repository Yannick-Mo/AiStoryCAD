import os
import uuid
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.config import settings
from app.project.models import Base
from app.settings.service import local_user


def _test_database_url() -> str:
    """测试库 URL：优先 TEST_DATABASE_URL 环境变量，否则在默认库名上派生 storyforge_test。

    绝不允许直接在开发/生产库上跑测试（历史事故：drop_all 清空整库）。
    """
    override = os.getenv("TEST_DATABASE_URL")
    if override:
        return override
    base = settings.database_url
    return base.rsplit("/", 1)[0] + "/storyforge_test"


async def _ensure_test_database(url: str) -> None:
    """测试库不存在则创建（通过 postgres 管理库执行 CREATE DATABASE）。"""
    db_name = url.rsplit("/", 1)[-1]
    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    engine = create_async_engine(admin_url, echo=False, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _prepare_test_database():
    url = _test_database_url()
    # 重写 settings，使被测代码路径（get_db / app.database.engine 等）也指向测试库
    settings.database_url = url
    await _ensure_test_database(url)
    # pgvector 扩展：知识库/检索模型需要
    engine = create_async_engine(url, echo=False, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        await engine.dispose()
    yield


@pytest_asyncio.fixture
async def db_session():
    url = _test_database_url()
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        yield session
        await session.close()
        await conn.rollback()
    # 历史上遗留、不在 Base.metadata 注册集合里的表（被测模块按需 import），
    # drop_all 不会清理它们，而它们的 FK 指向 scenes → 必须在 drop_all 之前
    # CASCADE 清理，否则 DROP TABLE scenes 触发 DependentObjectsStillExistError。
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
    """Single-user local tool: the fixed local identity (UUID id like the
    legacy user fixture, so callers can pass it straight to UUID columns)."""
    from uuid import UUID
    from app.settings.service import local_user
    u = local_user()
    return {**u, "id": UUID(u["id"])}
