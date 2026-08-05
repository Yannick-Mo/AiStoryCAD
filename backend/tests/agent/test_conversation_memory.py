"""Tests for DB-backed ConversationMemory (PostgreSQL source of truth,
Redis as cache)."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.memory.conversation import ConversationMemory
from app.agent.memory.models import Conversation, ConversationMessage
from app.llm.types import Message
from app.project.models import Project
import app.storycad.models  # noqa: F401  (register tables for db_session fixture)
import app.knowledge.models  # noqa: F401
import app.agent.consistency.orm  # noqa: F401


@pytest.fixture
async def project(db_session: AsyncSession, test_user: dict) -> uuid.UUID:
    p = Project(owner_id=uuid.UUID(str(test_user["id"])), title="Memory Test Project")
    db_session.add(p)
    await db_session.commit()
    return p.id


class TestConversationPersistence:
    async def test_create_and_list(self, db_session, project, test_user):
        mem = ConversationMemory(db_session)
        conv_id = await mem.create_conversation(str(project), str(test_user["id"]), "First chat")
        assert conv_id

        convs = await mem.list_conversations(str(project), str(test_user["id"]))
        assert any(c["id"] == conv_id for c in convs)
        row = await db_session.get(Conversation, uuid.UUID(conv_id))
        assert row is not None
        assert row.title == "First chat"

    async def test_save_and_get_history(self, db_session, project, test_user):
        mem = ConversationMemory(db_session)
        conv_id = await mem.create_conversation(str(project), str(test_user["id"]))
        await mem.save_message(conv_id, Message(role="user", content="你好"))
        await mem.save_message(conv_id, Message(role="assistant", content="你好！"))

        history = await mem.get_history(conv_id)
        assert len(history) == 2
        assert history[0].role == "user"
        assert history[0].content == "你好"

        rows = await db_session.execute(
            __import__("sqlalchemy").select(ConversationMessage).where(
                ConversationMessage.conversation_id == uuid.UUID(conv_id)
            )
        )
        assert len(list(rows.scalars().all())) == 2

    async def test_replace_history(self, db_session, project, test_user):
        mem = ConversationMemory(db_session)
        conv_id = await mem.create_conversation(str(project), str(test_user["id"]))
        await mem.save_message(conv_id, Message(role="user", content="old"))

        await mem.replace_history(
            conv_id,
            [Message(role="system", content="summary"), Message(role="user", content="recent")],
        )
        history = await mem.get_history(conv_id)
        assert len(history) == 2
        assert history[0].content == "summary"

    async def test_agent_state_roundtrip(self, db_session, project, test_user):
        mem = ConversationMemory(db_session)
        conv_id = await mem.create_conversation(str(project), str(test_user["id"]))
        await mem.save_agent_state(
            conv_id,
            {"steps": [{"tool": "x"}]},
            [{"option": "a"}],
            plan_confirmed=True,
            mode="plan",
            cowriter_session={"k": "v"},
            id_registry={"scene-1": "id-1"},
            id_registry_version=3,
        )
        state = await mem.load_agent_state(conv_id)
        pending, options, confirmed, mode, session, registry, version = state
        assert pending == {"steps": [{"tool": "x"}]}
        assert options == [{"option": "a"}]
        assert confirmed is True
        assert mode == "plan"
        assert session == {"k": "v"}
        assert registry == {"scene-1": "id-1"}
        assert version == 3

    async def test_summary_count_persisted(self, db_session, project, test_user):
        mem = ConversationMemory(db_session)
        conv_id = await mem.create_conversation(str(project), str(test_user["id"]))
        assert await mem.get_summary_count(conv_id) == 0
        await mem.set_summary_count(conv_id, 42)
        # New memory instance must still see it (source of truth is DB)
        mem2 = ConversationMemory(db_session)
        assert await mem2.get_summary_count(conv_id) == 42

    async def test_delete_scoped_to_owner(self, db_session, project, test_user):
        mem = ConversationMemory(db_session)
        conv_id = await mem.create_conversation(str(project), str(test_user["id"]))
        await mem.save_message(conv_id, Message(role="user", content="x"))

        other_user = str(uuid.uuid4())
        assert await mem.delete_conversation(str(project), other_user, conv_id) is False
        assert await mem.get_conversation(str(project), str(test_user["id"]), conv_id) is not None

        assert await mem.delete_conversation(str(project), str(test_user["id"]), conv_id) is True
        assert await mem.get_conversation(str(project), str(test_user["id"]), conv_id) is None
        row = await db_session.get(Conversation, uuid.UUID(conv_id))
        assert row is None

    async def test_get_conversation_ownership(self, db_session, project, test_user):
        mem = ConversationMemory(db_session)
        conv_id = await mem.create_conversation(str(project), str(test_user["id"]), "t")
        await mem.save_message(conv_id, Message(role="user", content="hi"))

        detail = await mem.get_conversation(str(project), str(test_user["id"]), conv_id)
        assert detail is not None
        assert detail["title"] == "t"
        assert len(detail["messages"]) == 1

        assert await mem.get_conversation(str(project), str(uuid.uuid4()), conv_id) is None
