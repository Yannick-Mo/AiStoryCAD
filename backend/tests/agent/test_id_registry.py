"""Tests for the per-turn entity ID registry (id_registry.py).

Covers the v2 design:
- flat entity extraction (``read_character`` / ``create_*`` …) + nested container
  extraction (``acts`` → ``chapters``, ``open_setups`` …)
- write-tool refreshes via both ``id`` and ``<entity>_id`` result keys
- composite labels for relations, entities without labels are skipped
- best-label retention
- cascade-delete eviction (direct id + child ids)
- snapshot pruning (filtered / truncated list results never replace the type)
- cross-request persistence merge (window overrides, ``seen`` expiry)
- params injection into tool results (StreamingToolExecutor integration)
- bounds (MAX_ENTRIES) and render output format

实体词表随数据模型一起收窄：``act`` · ``chapter`` · ``character`` · ``relation`` ·
``setup``。场景层（``scene``）与章间连线（``edge``）都已删除，这里没有任何引用。
"""

import asyncio
import inspect
import json


from app.agent.id_registry import (
    MAX_ENTRIES,
    RENDER_MAX_CHARS,
    STALE_WINDOW,
    build_id_registry,
    render_id_registry,
)
from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolMeta, ToolResult
from app.agent.tools.streaming_executor import StreamingToolExecutor


class FakeReadTool(BaseTool):
    """A minimal SAFE tool for executor integration tests."""

    meta = ToolMeta(
        name="fake_read",
        description="Fake read tool",
        parameters={"type": "object", "properties": {}},
        concurrency=ConcurrencyMode.SAFE,
    )

    async def run(self, db, **kwargs):
        return ToolResult(success=True, data={"id": "x1", "title": "X"})


def result(tool: str, data, success: bool = True, params: dict | None = None) -> dict:
    """Build a tool result dict shaped like what streaming_executor produces."""
    r = {"tool": tool, "success": success, "data": json.dumps(data, ensure_ascii=False)}
    if params is not None:
        r["params"] = params
    return r


def label(reg, etype: str, eid: str) -> str:
    return reg[etype][eid]["label"]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_flat_list_extraction():
    reg = build_id_registry([
        result("read_chapters", {
            "chapters": [
                {"id": "c1", "title": "第一章"},
                {"id": "c2", "title": "第二章"},
            ]
        }),
    ])
    assert label(reg, "chapter", "c1") == "第一章"
    assert label(reg, "chapter", "c2") == "第二章"
    assert len(reg["chapter"]) == 2


def test_nested_container_extraction():
    """幕里嵌着章清单时，嵌套的章也要收进来（read_acts / 框架树的形状）。"""
    reg = build_id_registry([
        result("read_acts", {
            "acts": [{
                "id": "a1", "name": "第一部",
                "chapters": [{"id": "ch1", "title": "第1章"}],
            }],
            "characters": [{"id": "c1", "name": "林晓"}],
            "setups": [{"id": "st1", "note": "旧硬币"}],
        }),
    ])
    assert label(reg, "act", "a1") == "第一部"
    assert label(reg, "chapter", "ch1") == "第1章"
    assert label(reg, "character", "c1") == "林晓"
    assert label(reg, "setup", "st1") == "旧硬币"


def test_flat_entity_tools_extraction():
    """单实体工具的结果是顶层 dict，靠 ``id`` 或 ``<entity>_id`` 认出来。"""
    reg = build_id_registry([
        result("read_character", {"id": "c9", "name": "苏菲"}),
        result("update_chapter", {"chapter_id": "ch9", "title": "归来"}),
        result("create_setup", {"setup_id": "st9", "note": "旧硬币"}),
    ])
    assert label(reg, "character", "c9") == "苏菲"
    assert label(reg, "chapter", "ch9") == "归来"
    assert label(reg, "setup", "st9") == "旧硬币"


def test_entity_without_label_is_skipped():
    reg = build_id_registry([result("read_character", {"id": "c0"})])
    assert reg == {}


def test_scene_and_edge_entity_types_are_gone():
    """场景 / 连线的容器键不再有任何意义，即便伪造结果也不该进表。"""
    reg = build_id_registry([result("legacy_tool", {
        "scenes": [{"id": "s1", "title": "开场"}],
        "edges": [{"id": "e1", "label": "因为"}],
    })])
    assert reg == {}


# ---------------------------------------------------------------------------
# Write-tool refreshes
# ---------------------------------------------------------------------------


def test_write_tool_refreshes_via_bare_id_key():
    reg = build_id_registry([
        result("create_character", {"id": "c5", "name": "初始名"}),
        result("update_act", {"id": "a5", "name": "改名后"}),
    ])
    assert label(reg, "character", "c5") == "初始名"
    assert label(reg, "act", "a5") == "改名后"


def test_write_tool_refreshes_via_entity_id_key():
    reg = build_id_registry([
        result("update_chapter", {"chapter_id": "ch7", "title": "第七章"}),
        result("update_relation", {"relation_id": "r7", "label": "敌对"}),
        result("create_setup", {"setup_id": "st7", "note": "旧硬币"}),
    ])
    assert label(reg, "chapter", "ch7") == "第七章"
    assert label(reg, "relation", "r7") == "敌对: r7"
    assert label(reg, "setup", "st7") == "旧硬币"


def test_best_label_retained():
    reg = build_id_registry([
        result("create_character", {"id": "c5", "name": "旧名字"}),
        result("read_character", {"id": "c5", "name": "新名字"}),
    ])
    assert label(reg, "character", "c5") == "新名字"


def test_best_label_prefers_longer_meaningful_name():
    reg = build_id_registry([
        result("read_character", {"id": "c5", "name": "林晓"}),
        result("update_character", {"id": "c5", "name": "林晓的宿敌"}),
    ])
    assert label(reg, "character", "c5") == "林晓的宿敌"


# ---------------------------------------------------------------------------
# Relations composite labels
# ---------------------------------------------------------------------------


def test_relation_composite_label_from_a_browse_snapshot():
    reg = build_id_registry([
        result("list_relations", {
            "relations": [{
                "id": "r1", "character_name": "林晓", "target_name": "苏菲",
                "rel_type": "friendship", "label": "好友",
            }],
        }),
    ])
    assert label(reg, "relation", "r1") == "好友: 林晓 → 苏菲"


def test_relation_label_without_names_falls_back_to_rel_type():
    reg = build_id_registry([
        result("list_relations", {
            "relations": [{
                "id": "r1", "character_id": "c1", "target_id": "c2",
                "rel_type": "friendship",
            }],
        }),
    ])
    assert label(reg, "relation", "r1") == "friendship: r1"


def test_relation_composite_label_from_a_flat_create():
    """``create_relation`` 返回扁平 dict，两端名字也在里面。"""
    reg = build_id_registry([
        result("create_relation", {
            "relation_id": "r9", "character_name": "林晓", "target_name": "苏菲",
            "rel_type": "friendship", "label": "好友",
        }),
    ])
    assert label(reg, "relation", "r9") == "好友: 林晓 → 苏菲"


# ---------------------------------------------------------------------------
# Cascade-delete eviction
# ---------------------------------------------------------------------------


def test_delete_act_evicts_the_act_and_its_chapters():
    base = build_id_registry([
        result("read_acts", {
            "acts": [{"id": "a1", "name": "第一部",
                      "chapters": [{"id": "ch1", "title": "第1章"},
                                   {"id": "ch2", "title": "第2章"}]}],
            "characters": [{"id": "c1", "name": "林晓"}],
        }),
    ])
    assert set(base) == {"act", "chapter", "character"}

    reg = build_id_registry([
        result("delete_act", {
            "deleted_act_id": "a1",
            "deleted_chapter_ids": ["ch1", "ch2"],
        }),
    ], persisted=base)
    assert "act" not in reg
    assert "chapter" not in reg
    assert label(reg, "character", "c1") == "林晓"


def test_delete_character_evicts_the_character_and_its_relations():
    """ID 寄存表按 ``deleted_relation_ids`` 级联清理关系条目。"""
    base = build_id_registry([
        result("create_character", {"character_id": "c1", "name": "林晓"}),
        result("create_character", {"character_id": "c2", "name": "苏菲"}),
        result("create_relation", {"relation_id": "r1", "character_id": "c1",
                                   "target_id": "c2", "label": "好友"}),
    ])
    reg = build_id_registry([
        result("delete_character", {"deleted_character_id": "c1",
                                   "deleted_relation_ids": ["r1"]}),
    ], persisted=base)
    assert "c1" not in reg["character"]
    assert label(reg, "character", "c2") == "苏菲"
    assert "relation" not in reg


def test_delete_character_tool_reports_the_relations_it_cascaded():
    from app.agent.tools.character_tools import DeleteCharacterTool

    src = inspect.getsource(DeleteCharacterTool.run)
    assert "deleted_relation_ids" in src


def test_delete_chapter_evicts_the_chapter():
    base = build_id_registry([
        result("read_chapters", {"chapters": [{"id": "ch1", "title": "第1章"},
                                              {"id": "ch2", "title": "第2章"}]}),
    ])
    reg = build_id_registry([
        result("delete_chapter", {"deleted_chapter_id": "ch1"}),
    ], persisted=base)
    assert "ch1" not in reg["chapter"]
    assert label(reg, "chapter", "ch2") == "第2章"


def test_delete_relation_evicts_direct_id():
    base = build_id_registry([
        result("create_relation", {"relation_id": "r1", "character_id": "c1",
                                   "target_id": "c2", "label": "好友"}),
    ])
    reg = build_id_registry(
        [result("delete_relation", {"deleted_relation_id": "r1"})], persisted=base)
    assert "relation" not in reg


def test_single_character_survives_relation_delete():
    base = build_id_registry([
        result("create_character", {"character_id": "c1", "name": "林晓"}),
        result("create_relation", {"relation_id": "r1", "character_id": "c1",
                                   "target_id": "c2", "label": "好友"}),
    ])
    reg = build_id_registry(
        [result("delete_relation", {"deleted_relation_id": "r1"})], persisted=base)
    assert label(reg, "character", "c1") == "林晓"
    assert "relation" not in reg


def test_setup_snapshot_prunes_entries_that_vanished():
    """list_setups 的全量快照会淘汰不再出现的条目（覆盖「伏笔在别处被删掉」的情形）。"""
    base = build_id_registry([
        result("list_setups", {"setups": [{"id": "st1", "note": "旧硬币"},
                                          {"id": "st2", "note": "刀"}]}),
    ])
    reg = build_id_registry([
        result("list_setups", {"setups": [{"id": "st1", "note": "旧硬币"}]}),
    ], persisted=base)
    assert "st2" not in reg["setup"], "全量快照必须淘汰不再出现的条目"
    assert label(reg, "setup", "st1") == "旧硬币"


def test_delete_setup_evicts_direct_id():
    base = build_id_registry([
        result("create_setup", {"setup_id": "st1", "note": "旧硬币"}),
        result("create_setup", {"setup_id": "st2", "note": "刀"}),
    ])
    reg = build_id_registry(
        [result("delete_setup", {"deleted_setup_id": "st1"})], persisted=base)
    assert "st1" not in reg["setup"], "删掉的伏笔不能留在 ID 寄存表里"
    assert label(reg, "setup", "st2") == "刀"


# ---------------------------------------------------------------------------
# Snapshot pruning
# ---------------------------------------------------------------------------


def test_snapshot_replaces_unfiltered():
    base = build_id_registry([
        result("list_characters", {"characters": [{"id": "c1", "name": "旧"}]}),
    ])
    reg = build_id_registry([
        result("list_characters", {"characters": [{"id": "c2", "name": "新"}]}),
    ], persisted=base)
    assert "c1" not in reg["character"]
    assert label(reg, "character", "c2") == "新"
    assert len(reg["character"]) == 1


def test_filtered_snapshot_does_not_replace():
    """带筛选参数的 list_setups 只 upsert —— 它不是一个权威全量。"""
    base = build_id_registry([
        result("list_setups", {"setups": [{"id": "st1", "note": "甲"}]}),
    ])
    reg = build_id_registry([
        result("list_setups", {"setups": [{"id": "st2", "note": "乙"}]},
               params={"status": "paid"}),
    ], persisted=base)
    assert "st1" in reg["setup"]
    assert "st2" in reg["setup"]


def test_single_relation_read_does_not_replace_type():
    # read_relation returns ONE relation (a flat dict, not a snapshot) — it must
    # upsert, never prune the persisted type like an unfiltered list_ would.
    base = build_id_registry([
        result("list_relations", {"relations": [{"id": "r1", "character_id": "c1",
                                                 "target_id": "c2", "label": "甲"}]}),
    ])
    reg = build_id_registry([
        result("read_relation", {"id": "r9", "character_id": "c1", "target_id": "c2",
                                 "label": "精读条"}),
    ], persisted=base)
    assert "r1" in reg["relation"]
    assert "r9" in reg["relation"]


def test_truncated_snapshot_does_not_replace():
    base = build_id_registry([
        result("list_setups", {"setups": [{"id": "st1", "note": "甲"}]}),
    ])
    truncated_body = '{"setups": [{"id": "st2", "note": "乙"}'
    r = {"tool": "list_setups", "success": True,
         "data": '[truncated, remaining omitted]\n' + truncated_body}
    reg = build_id_registry([r], persisted=base)
    assert "st2" not in reg["setup"]
    assert label(reg, "setup", "st1") is not None


def test_older_snapshot_does_not_resurrect_stale_entries():
    base = build_id_registry([
        result("list_characters", {"characters": [{"id": "c1", "name": "旧"}]}),
    ])
    reg = build_id_registry([
        result("list_characters", {"characters": [{"id": "c1", "name": "旧"}]}),
        result("list_characters", {"characters": [{"id": "c2", "name": "新"}]}),
    ], persisted=base)
    # newest snapshot replaces the type; older snapshot must not resurrect c1
    assert "c1" not in reg["character"]
    assert label(reg, "character", "c2") == "新"


def test_detail_read_and_later_browse_snapshot_coexist():
    # list_relations（浏览，无参数）是关系唯一的权威快照 —— 它按自己在窗口里的
    # 位置替换整类；而明细读取（read_relation）只 upsert，所以两者都不丢。
    reg = build_id_registry([
        result("read_relation", {"id": "r2", "character_id": "c9", "target_id": "c2",
                                 "label": "新细读"}),
        result("list_relations", {"relations": [{"id": "r0", "character_id": "c1",
                                                 "target_id": "c2", "label": "全量浏览"}]}),
    ])
    assert "r0" in reg["relation"]
    assert "r2" in reg["relation"]


# ---------------------------------------------------------------------------
# Persistence merge
# ---------------------------------------------------------------------------


def test_window_overrides_persisted():
    base = build_id_registry([
        result("read_character", {"id": "c1", "name": "旧名"}),
    ], version=1)
    reg = build_id_registry([
        result("update_character", {"id": "c1", "name": "新名"}),
    ], persisted=base, version=2)
    assert label(reg, "character", "c1") == "新名"


def test_seen_expiry_with_stale_version():
    base = build_id_registry(
        [result("read_character", {"id": "c_old", "name": "久远"})],
        version=1,
    )
    window = [
        result("create_character", {"id": f"c_new{i}", "name": f"新人{i}"})
        for i in range(2)
    ]
    reg = build_id_registry(window, persisted=base, version=1 + STALE_WINDOW + 1)
    assert "c_old" not in reg["character"]
    for i in range(2):
        assert f"c_new{i}" in reg["character"]


def test_empty_persisted_and_window():
    reg = build_id_registry([], persisted=None)
    assert reg == {}


def test_failed_result_is_ignored():
    reg = build_id_registry([result("read_character", {"id": "c1", "name": "x"}, success=False)])
    assert reg == {}


def test_read_progress_surfaces_setup_ids():
    """read_progress 把伏笔拆成两组返回，两组的元素都带 id。"""
    reg = build_id_registry([
        result("read_progress", {
            "open_setups": [{"id": "st1", "note": "旧硬币"}],
            "overdue_setups": [{"id": "st2", "note": "刀"}],
        }),
    ])
    assert label(reg, "setup", "st1") == "旧硬币"
    assert label(reg, "setup", "st2") == "刀"


def test_read_progress_surfaces_recently_changed_chapter_ids():
    reg = build_id_registry([
        result("read_progress", {
            "recently_changed_chapters": [
                {"id": "c1", "global_order": 1, "title": "第一章"},
            ],
        }),
    ])
    assert label(reg, "chapter", "c1") == "第一章"


def test_non_json_data_is_ignored():
    r = {"tool": "list_relations", "success": True, "data": "not json at all"}
    assert build_id_registry([r]) == {}


def test_bounds_trim_large_registry():
    results = [
        result("create_character", {"character_id": f"c{i}", "name": f"角色{i}"})
        for i in range(MAX_ENTRIES + 50)
    ]
    reg = build_id_registry(results)
    total = sum(len(v) for v in reg.values())
    assert total <= MAX_ENTRIES


def test_window_slides_over_old_results():
    # Results outside the trailing window (WINDOW_SIZE) are ignored.
    results = [
        result("create_character", {"character_id": f"c{i}", "name": f"角色{i}"})
        for i in range(30)
    ]
    reg = build_id_registry(results)
    assert "c0" not in reg["character"]
    assert "c29" in reg["character"]


# ---------------------------------------------------------------------------
# Params injection (streaming executor integration)
# ---------------------------------------------------------------------------


def test_executor_injects_params_into_results():
    fake_tool = FakeReadTool()
    executor = StreamingToolExecutor(
        tools={"fake_read": fake_tool}, db=None,
        project_id="p1", user_id="u1",
    )
    result_dict = asyncio.run(executor._execute_tool("fake_read", {"limit": 3}))
    assert result_dict["tool"] == "fake_read"
    assert result_dict["success"] is True
    assert result_dict["params"] == {"limit": 3, "project_id": "p1", "user_id": "u1"}


def test_executor_params_drive_filter_detection():
    reg = build_id_registry([
        result("list_setups", {"setups": [{"id": "st1", "note": "甲"}]},
               params={"status": "open"}),
    ])
    # filtered snapshot upserts instead of replacing
    assert "st1" in reg["setup"]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_groups_by_type():
    reg = build_id_registry([
        result("list_characters", {"characters": [
            {"id": "c1", "name": "甲角色"},
            {"id": "c2", "name": "乙角色"},
        ]}),
    ])
    out = render_id_registry(reg)
    assert "# --- 已知实体 ID" in out
    assert "## 人物（2）" in out
    assert "  甲角色 | c1" in out
    assert "  乙角色 | c2" in out
    assert "（注：ID 可能已过期" in out


def test_render_empty_returns_empty_string():
    assert render_id_registry({}) == ""


def test_render_respects_char_limit():
    reg = build_id_registry([
        result("create_character", {"character_id": "c1", "name": "长" * 1000}),
    ])
    out = render_id_registry(reg)
    assert len(out) <= RENDER_MAX_CHARS
