from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_pre_ping=True,
    connect_args={"server_settings": {"statement_timeout": "30000"}},
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    from app.project.models import Base
    from app.storycad.models import Act, Chapter, Scene, SceneContent, Character, CharacterRelation, Theme, ThemeChapter, ChapterEdge, ChapterRhythm  # noqa: F401
    from app.knowledge.models import KnowledgeChunk  # noqa: F401
    from app.settings.models import ModelConfig  # noqa: F401
    from app.agent.consistency.orm import (  # noqa: F401
        ConflictCandidateRecord,
        ConsistencyFact,
        ConsistencyTimeCache,
        FactQueueItem,
        ConsistencyReportRecord,
        ConsistencyLog,
    )
    from app.agent.memory.models import (  # noqa: F401
        Conversation,
        ConversationMessage,
    )
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
