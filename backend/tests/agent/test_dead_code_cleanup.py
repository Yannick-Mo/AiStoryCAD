"""Regression tests for low-priority dead-code cleanup and the
create-from-material chapter-drop fix."""

import inspect
import uuid

from sqlalchemy import func, select

from app.storycad.models import Chapter, Act
from app.agent.project_creator.writer import write_project_from_state


class TestMaterialWriterHasNoChapterCap:
    """create-from-material 曾经在**两条**写库路径里都悄悄丢掉每章第 5 章之后的章。

    现在只剩一条写库路径：``app/agent/project_creator/writer.py::write_project_from_state``
    （``routes_ai`` 与 ``create_project_from_material`` 都调它）。流水线产出的每一章都必须
    落库；真的丢东西只允许发生在记账里——``write_project_from_state`` 返回的 ``truncated``
    必须如实列出。
    """

    def test_writer_has_no_per_chapter_cap_in_source(self):
        src = inspect.getsource(write_project_from_state)
        assert "per_chapter_count" not in src
        assert "> 5" not in src
        assert "chapters[:5]" not in src

    def test_the_two_old_writers_are_gone(self):
        """旧的两条写库路径已合并删除：一个模块、一个函数。"""
        import importlib.util

        from app.api import routes_ai

        assert not hasattr(routes_ai, "_write_project_to_db")
        assert importlib.util.find_spec("app.agent.tools.project_admin_tools") is None

    def test_both_entry_points_use_the_single_writer(self):
        from app.api import routes_ai
        from app.agent.tools.project_tools import CreateProjectFromMaterialTool

        assert "write_project_from_state" in inspect.getsource(routes_ai)
        assert "write_project_from_state" in inspect.getsource(
            CreateProjectFromMaterialTool.run
        )

    async def test_twelve_chapters_in_one_act_all_persist(self, db_session, test_user):
        state = {
            "project_title": "素材项目",
            "acts": [{"name": "第一幕", "purpose": "这一幕要干什么"}],
            "chapters": [
                {"title": f"第{i + 1}章", "goal": f"第{i + 1}章要写什么", "act_index": 0}
                for i in range(12)
            ],
        }
        result = await write_project_from_state(db_session, state, test_user["id"])

        assert result["chapter_count"] == 12
        assert result["truncated"] == [], f"没有丢任何数据：{result['truncated']}"
        stored = (await db_session.execute(
            select(func.count()).select_from(Chapter)
            .where(Chapter.project_id == uuid.UUID(result["project_id"]))
        )).scalar_one()
        assert stored == 12, "每章第 5 章之后的章又丢了"

    async def test_chapters_are_numbered_densely_inside_their_act(self, db_session, test_user):
        state = {
            "project_title": "两幕项目",
            "acts": [{"name": "第一幕", "purpose": "甲"}, {"name": "第二幕", "purpose": "乙"}],
            "chapters": [
                {"title": f"A{i + 1}", "goal": "要写什么", "act_index": 0} for i in range(7)
            ] + [
                {"title": f"B{i + 1}", "goal": "要写什么", "act_index": 1} for i in range(7)
            ],
        }
        result = await write_project_from_state(db_session, state, test_user["id"])
        project_id = uuid.UUID(result["project_id"])

        acts = (await db_session.execute(
            select(Act).where(Act.project_id == project_id)
        )).scalars().all()
        assert [a.sort_order for a in acts] == [1, 2]
        for act in acts:
            orders = (await db_session.execute(
                select(Chapter.sort_order).where(Chapter.act_id == act.id)
            )).scalars().all()
            assert sorted(orders) == list(range(1, 8)), "幕内序号必须密集从 1 开始"

    async def test_a_chapter_with_an_out_of_range_act_index_is_reported_not_dropped(self, db_session, test_user):
        """act_index 越界的章会被跳过——但必须写进 truncated，不能静默丢。"""
        state = {
            "project_title": "越界",
            "acts": [{"name": "第一幕", "purpose": "甲"}],
            "chapters": [
                {"title": "好章", "goal": "要写什么", "act_index": 0},
                {"title": "坏章", "goal": "要写什么", "act_index": 9},
            ],
        }
        result = await write_project_from_state(db_session, state, test_user["id"])
        assert result["chapter_count"] == 1
        assert any("坏章" in note for note in result["truncated"]), result["truncated"]


class TestDeadCodeRemoved:
    """Dead code identified in the audit is gone."""

    def test_attachments_collect_removed(self):
        import app.agent.attachments as att

        assert not hasattr(att.AttachmentInjector, "collect")
        assert not hasattr(att.AttachmentInjector, "_summarize_tool_results")
        assert not hasattr(att.AttachmentInjector, "_format_session_progress")

    def test_streaming_executor_dead_sets_removed(self):
        import app.agent.tools.streaming_executor as se

        assert not hasattr(se, "_JSON_OUTPUT_TOOLS")
        # _blocked_writes instance attr must not be initialized
        src = inspect.getsource(se.StreamingToolExecutor.__init__)
        assert "_blocked_writes" not in src

    def test_loop_no_duplicate_rag_cap(self):
        import app.agent.loop as loop_mod

        assert not hasattr(loop_mod, "MAX_RAG_CHARS")
        # response_builder keeps the single source of truth
        import app.agent.response_builder as rb

        assert hasattr(rb, "MAX_RAG_CHARS")

    def test_conversation_dead_methods_removed(self):
        from app.agent.memory.conversation import ConversationMemory

        assert not hasattr(ConversationMemory, "get_or_create_conversation")
        assert not hasattr(ConversationMemory, "delete_last_message")

    def test_empty_schema_stub_removed(self):
        import importlib.util

        spec = importlib.util.find_spec("app.agent.schema")
        assert spec is None
