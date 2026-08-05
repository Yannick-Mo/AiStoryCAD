"""Regression tests for low-priority dead-code cleanup and the
create-from-material scene-drop fix."""

import inspect


class TestSceneDropFix:
    """create-from-material silently dropped scenes beyond 5 per chapter in
    both DB writers.  All generated scenes must be persisted."""

    def _read_writer_src(self, module_path: str, func_name: str) -> str:
        import importlib

        mod = importlib.import_module(module_path)
        return inspect.getsource(getattr(mod, func_name))

    def test_route_writer_has_no_scene_cap(self):
        src = self._read_writer_src("app.api.routes_ai", "_write_project_to_db")
        assert "per_chapter_count" not in src
        assert "> 5" not in src

    def test_tool_writer_has_no_scene_cap(self):
        src = self._read_writer_src(
            "app.agent.tools.project_admin_tools", "_write_new_project"
        )
        assert "per_chapter_count" not in src
        assert "> 5" not in src


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
