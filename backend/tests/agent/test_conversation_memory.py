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
        pending, options, confirmed, mode, session, registry, version, snapshot = state
        assert pending == {"steps": [{"tool": "x"}]}
        assert options == [{"option": "a"}]
        assert confirmed is True
        assert mode == "plan"
        assert session == {"k": "v"}
        assert registry == {"scene-1": "id-1"}
        assert version == 3
        assert snapshot is None

    async def test_loop_snapshot_roundtrip(self, db_session, project, test_user):
        from app.agent.memory.conversation import (
            ConversationMemory,
            _msg_to_dict,
            _dict_to_msg,
        )
        from app.llm.types import ToolCall
        mem = ConversationMemory(db_session)
        conv_id = await mem.create_conversation(str(project), str(test_user["id"]))
        msgs = [
            Message(role="user", content="帮我重建时间线"),
            Message(role="assistant", content="我需要执行写入操作", tool_calls=[
                ToolCall(id="call_1", function={"name": "update_project", "arguments": "{}"}),
            ]),
        ]
        snapshot = {
            "project_id": str(project),
            "user_id": str(test_user["id"]),
            "conversation_id": conv_id,
            "mode": "cowriter",
            "messages": [_msg_to_dict(m) for m in msgs],
            "tool_results": [{"tool": "read_project", "success": True}],
            "project_context": {"project": {"title": "test"}},
            "active_skills": ["mystery"],
            "_context_loaded": True,
            "_turn_count": 3,
        }
        await mem.save_agent_state(
            conv_id,
            {"steps": [{"tool": "update_project"}]},
            [],
            plan_confirmed=False,
            mode="cowriter",
            id_registry={},
            id_registry_version=2,
            loop_snapshot=snapshot,
        )
        # New memory instance must restore the full snapshot
        mem2 = ConversationMemory(db_session)
        pending, options, confirmed, mode, session, registry, version, snap2 = (
            await mem2.load_agent_state(conv_id)
        )
        assert pending == {"steps": [{"tool": "update_project"}]}
        assert confirmed is False
        assert snap2["mode"] == "cowriter"
        assert snap2["tool_results"] == [{"tool": "read_project", "success": True}]
        restored = [_dict_to_msg(m) for m in snap2["messages"]]
        assert restored[0].content == "帮我重建时间线"
        assert restored[1].tool_calls[0].id == "call_1"
        assert snap2["_turn_count"] == 3

    async def test_loop_snapshot_helpers_roundtrip(self):
        """_snapshot_to_dict / _snapshot_from_dict must survive JSON round-trip
        and preserve the full loop in-memory context."""
        import json
        from app.agent.super_agent import _snapshot_from_dict, _snapshot_to_dict
        from app.llm.types import ToolCall

        final_values = {
            "project_id": "p1",
            "user_id": "u1",
            "conversation_id": "c1",
            "mode": "cowriter",
            "messages": [
                Message(role="user", content="重建时间线"),
                Message(role="assistant", content=None, tool_calls=[
                    ToolCall(id="call_1", function={"name": "delete_edge", "arguments": "{}"}),
                ]),
            ],
            "tool_results": [{"tool": "read_project", "success": True}],
            "project_context": {"project": {"title": "test"}},
            "active_skills": ["mystery"],
            "cowriter_session": {"phase": "explore"},
            "current_options": [{"k": "v"}],
            "id_registry": {"scene-1": "id-1"},
            "id_registry_version": 2,
            "pending_plan": {"steps": [{"tool": "delete_edge"}]},
            "plan_confirmed": False,
            "_context_loaded": True,
            "_turn_count": 3,
            "_invalidated_sections": ["structure"],
        }

        serialized = _snapshot_to_dict(final_values)
        # Must be JSON-safe (messages serialized to plain dicts)
        assert json.dumps(serialized, ensure_ascii=False)
        assert isinstance(serialized["messages"][0], dict)
        assert serialized["messages"][1]["tool_calls"][0]["id"] == "call_1"
        assert serialized["_invalidated_sections"] == ["structure"]

        restored = _snapshot_from_dict(serialized)
        assert restored["messages"][0].content == "重建时间线"
        assert restored["messages"][1].tool_calls[0].id == "call_1"
        assert restored["tool_results"][0]["tool"] == "read_project"
        assert restored["_context_loaded"] is True
        assert restored["pending_plan"]["steps"][0]["tool"] == "delete_edge"

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
