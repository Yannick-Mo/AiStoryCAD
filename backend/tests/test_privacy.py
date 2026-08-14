"""Tests for SSE event privacy / display-name sanitisation (app.agent.privacy).

Ensures internal tool function names, parameters, and error details never
leak to the frontend; all tool events are mapped to user-facing labels.
"""

import json

from app.agent.privacy import sanitise_event, TOOL_DISPLAY_NAMES


class TestToolDone:
    def test_maps_tool_name_and_strips_internal_id(self):
        td = sanitise_event("tool_done", json.dumps({
            "tool": "list_chapters",
            "success": True,
            "data": "x",
            "error": None,
            "_tool_use_id": "abc",
        }))
        d = json.loads(td)
        assert d["tool"] == "列出章节"
        assert "_tool_use_id" not in d

    def test_search_nodes_display_name(self):
        td = sanitise_event("tool_done", json.dumps({
            "tool": "search_nodes", "success": True, "data": "x",
        }))
        assert json.loads(td)["tool"] == "搜索节点"

    def test_unknown_tool_falls_back_to_generic_label(self):
        td = sanitise_event("tool_done", json.dumps({
            "tool": "some_future_tool", "success": True, "data": "x",
        }))
        assert json.loads(td)["tool"] == "执行操作"

    def test_known_error_message_is_shortened(self):
        td = sanitise_event("tool_done", json.dumps({
            "tool": "update_scene", "success": False,
            "error": "InFailedSQLTransactionError: current transaction is aborted",
        }))
        assert json.loads(td)["error"] == "数据库异常"

    def test_timeout_error_is_shortened(self):
        td = sanitise_event("tool_done", json.dumps({
            "tool": "web_fetch", "success": False, "error": "Connection timed out",
        }))
        assert json.loads(td)["error"] == "操作超时"

    def test_unmapped_fields_pass_through(self):
        td = sanitise_event("tool_done", json.dumps({
            "tool": "read_project", "success": True, "data": "keep-me",
        }))
        d = json.loads(td)
        assert d["tool"] == "读取项目"
        assert d["data"] == "keep-me"
        assert d["success"] is True


class TestPlan:
    def test_strips_params_and_tool_use_id(self):
        plan = sanitise_event("plan", json.dumps({
            "steps": [{
                "tool": "list_scenes",
                "params": {"project_id": "x"},
                "description": "列出场景",
                "tool_use_id": "t1",
            }],
            "reasoning": "需要列出场景",
            "status": "awaiting_confirmation",
        }))
        p = json.loads(plan)
        step = p["steps"][0]
        assert step["tool"] == "列出场景"
        assert "params" not in step
        assert "tool_use_id" not in step
        assert p["status"] == "awaiting_confirmation"

    def test_description_falls_back_to_display_name(self):
        plan = sanitise_event("plan", json.dumps({
            "steps": [{"tool": "list_scenes", "description": "list_scenes"}],
            "reasoning": "",
        }))
        step = json.loads(plan)["steps"][0]
        assert step["description"] == "列出场景"

    def test_reasoning_sanitises_internal_names(self):
        plan = sanitise_event("plan", json.dumps({
            "steps": [{"tool": "read_scene", "description": "读取场景"}],
            "reasoning": "调用 function 'read_scene' 分析后继续",
        }))
        p = json.loads(plan)
        assert "read_scene" not in p["reasoning"]
        assert "接口" in p["reasoning"]

    def test_malformed_json_passes_through(self):
        raw = "not-json"
        assert sanitise_event("plan", raw) == raw


class TestProjectUpdated:
    def test_display_names_and_strips_details(self):
        pu = sanitise_event("project_updated", json.dumps({
            "tools_executed": ["list_edges"],
            "tool_details": [{"name": "list_edges", "changes": {"data": "x"}}],
            "all_success": True,
        }))
        u = json.loads(pu)
        assert u["tools_executed"][0] == "列出关联"
        assert u["tool_details"] == [{"name": "列出关联"}]


class TestError:
    def test_plain_text_error_sanitised(self):
        e = sanitise_event("error", "Tool 'search_nodes' failed: DB error")
        assert e == "操作 failed: DB error"

    def test_json_error_dict_sanitised(self):
        e = sanitise_event("error", json.dumps({"message": "Tool 'list_scenes' failed"}))
        d = json.loads(e)
        assert "list_scenes" not in d["message"]
        assert "操作" in d["message"]

    def test_malformed_json_passes_through(self):
        raw = "raw-error"
        assert sanitise_event("error", raw) == raw


class TestPassthrough:
    def test_other_event_types_unchanged(self):
        raw = json.dumps({"a": 1})
        assert sanitise_event("token", raw) == raw
        assert sanitise_event("done", raw) == raw

    def test_all_known_tools_have_display_names(self):
        # 每个已知工具名都必须有非空的中文展示名（除 fallback 外不得为"执行操作"）
        for internal, display in TOOL_DISPLAY_NAMES.items():
            assert display, internal
            assert display != "执行操作", internal
