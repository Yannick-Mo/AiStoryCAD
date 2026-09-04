"""Tests for the per-turn entity ID registry (id_registry.py).

Covers the v2 design:
- flat entity extraction (list_* containers) + nested extraction (project reads)
- write-tool refreshes via both `id` and `<entity>_id` result keys
- composite labels for relations/edges, entities without labels are skipped
- best-label retention
- cascade-delete eviction (direct id + child ids)
- snapshot pruning (filtered / truncated list results never replace the type)
- cross-request persistence merge (window overrides, `seen` expiry)
- params injection into tool results (StreamingToolExecutor integration)
- bounds (MAX_ENTRIES) and render output format
"""

import asyncio
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


def test_nested_read_full_project_extraction():
    reg = build_id_registry([
        result("read_full_project", {
            "project": {"id": "p1", "title": "我的小说"},
            "acts": [{
                "id": "a1", "name": "第一部",
                "chapters": [{"id": "ch1", "title": "第1章"}],
            }],
            "characters": [{"id": "c1", "name": "林晓"}],
            "scenes": [{"id": "s1", "title": "开场"}],
        }),
    ])
    assert label(reg, "act", "a1") == "第一部"
    assert label(reg, "chapter", "ch1") == "第1章"
    assert label(reg, "character", "c1") == "林晓"
    assert label(reg, "scene", "s1") == "开场"


def test_flat_entity_tools_extraction():
    reg = build_id_registry([
        result("read_character", {"id": "c9", "name": "苏菲"}),
        result("read_scene", {"id": "s9", "title": "雨夜"}),
        result("read_chapter", {"id": "ch9", "title": "归来"}),
    ])
    assert label(reg, "character", "c9") == "苏菲"
    assert label(reg, "scene", "s9") == "雨夜"
    assert label(reg, "chapter", "ch9") == "归来"


def test_entity_without_label_is_skipped():
    reg = build_id_registry([result("read_character", {"id": "c0"})])
    assert reg == {}


# ---------------------------------------------------------------------------
# Write-tool refreshes
# ---------------------------------------------------------------------------


def test_write_tool_refreshes_via_id_key():
    reg = build_id_registry([
        result("create_character", {"id": "c5", "name": "初始名"}),
        result("update_scene", {"id": "s5", "title": "改名后"}),
    ])
    assert label(reg, "character", "c5") == "初始名"
    assert label(reg, "scene", "s5") == "改名后"


def test_write_tool_refreshes_via_entity_id_key():
    reg = build_id_registry([
        result("update_chapter", {"chapter_id": "ch7", "title": "第七章"}),
        result("update_relation", {"relation_id": "r7", "label": "敌对"}),
    ])
    assert label(reg, "chapter", "ch7") == "第七章"
    assert label(reg, "relation", "r7") == "敌对: r7"


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
# Relations / edges composite labels
# ---------------------------------------------------------------------------


def test_relation_composite_label_with_names():
    reg = build_id_registry([
        result("read_relations", {
            "relations": [{
                "id": "r1", "character_name": "林晓", "target_name": "苏菲",
                "rel_type": "friendship", "label": "好友",
            }],
        }),
    ])
    assert label(reg, "relation", "r1") == "好友: 林晓 → 苏菲"


def test_relation_label_without_names_falls_back_to_rel_type():
    reg = build_id_registry([
        result("read_relations", {
            "relations": [{
                "id": "r1", "character_id": "c1", "target_id": "c2",
                "rel_type": "friendship",
            }],
        }),
    ])
    assert label(reg, "relation", "r1") == "friendship: r1"


def test_edge_composite_label():
    reg = build_id_registry([
        result("list_edges", {
            "edges": [
                {"id": "e1", "source_title": "场景甲", "target_title": "场景乙",
                 "type": "因果", "label": "因为"},
            ],
        }),
    ])
    assert label(reg, "edge", "e1") == "因为: 场景甲 → 场景乙"


# ---------------------------------------------------------------------------
# Cascade-delete eviction
# ---------------------------------------------------------------------------


def test_delete_evicts_direct_and_child_ids():
    base = build_id_registry([
        result("read_full_project", {
            "project": {"id": "p1", "title": "小说"},
            "acts": [{"id": "a1", "name": "第一部",
                      "chapters": [{"id": "ch1", "title": "第1章",
                                    "scenes": [{"id": "s1", "title": "开场"}]}]}],
            "characters": [{"id": "c1", "name": "林晓", "relations": [
                {"id": "r1", "character_id": "c1", "target_id": "c2",
                 "rel_type": "friendship"}]}],
        }),
    ])
    assert set(base) == {"act", "chapter", "scene", "character", "relation"}

    reg = build_id_registry([
        result("delete_act", {
            "deleted_act_id": "a1",
            "deleted_chapter_ids": ["ch1"],
            "deleted_scene_ids": ["s1"],
        }),
    ], persisted=base)
    assert "act" not in reg
    assert "chapter" not in reg
    assert "scene" not in reg
    assert label(reg, "character", "c1") == "林晓"


def test_delete_character_cascades_relations():
    base = build_id_registry([
        result("create_character", {"id": "c1", "name": "林晓"}),
        result("create_character", {"id": "c2", "name": "苏菲"}),
        result("create_relation", {"id": "r1", "character_id": "c1", "target_id": "c2", "label": "好友"}),
    ])
    reg = build_id_registry([
        result("delete_character", {"character_id": "c1", "deleted_relation_ids": ["r1"]}),
    ], persisted=base)
    assert "c1" not in reg["character"]
    assert label(reg, "character", "c2") == "苏菲"
    assert "relation" not in reg


def test_delete_chapter_cascades_scenes():
    base = build_id_registry([
        result("read_full_project", {
            "project": {"id": "p1", "title": "小说"},
            "chapters": [{"id": "ch1", "title": "第1章",
                          "scenes": [{"id": "s1", "title": "开场"}, {"id": "s2", "title": "高潮"}]}],
        }),
    ])
    reg = build_id_registry([
        result("delete_chapter", {"deleted_chapter_id": "ch1", "deleted_scene_ids": ["s1", "s2"]}),
    ], persisted=base)
    assert "chapter" not in reg
    assert "scene" not in reg


def test_delete_relation_evicts_direct_id():
    base = build_id_registry([
        result("create_relation", {"id": "r1", "character_id": "c1", "target_id": "c2", "label": "好友"}),
    ])
    reg = build_id_registry([result("delete_relation", {"relation_id": "r1"})], persisted=base)
    assert "relation" not in reg


def test_delete_scene_evicts_scene_id():
    base = build_id_registry([
        result("create_scene", {"id": "s1", "title": "开场"}),
    ])
    reg = build_id_registry([result("delete_scene", {"deleted_scene_id": "s1"})], persisted=base)
    assert "scene" not in reg


def test_single_character_survives_relation_delete():
    base = build_id_registry([
        result("create_character", {"id": "c1", "name": "林晓"}),
        result("create_relation", {"id": "r1", "character_id": "c1", "target_id": "c2", "label": "好友"}),
    ])
    reg = build_id_registry([result("delete_relation", {"relation_id": "r1"})], persisted=base)
    assert label(reg, "character", "c1") == "林晓"
    assert "relation" not in reg


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
    base = build_id_registry([
        result("list_relations", {"relations": [{"id": "r1", "character_id": "c1", "target_id": "c2", "label": "甲"}]}),
    ])
    reg = build_id_registry([
        result("list_relations", {"relations": [{"id": "r2", "character_id": "c9", "target_id": "c2", "label": "乙"}]},
               params={"character_id": "c9"}),
    ], persisted=base)
    # filtered results only upsert — they must NOT replace the persisted type
    assert "r1" in reg["relation"]
    assert "r2" in reg["relation"]


def test_single_relation_read_does_not_replace_type():
    # relation_id mode returns ONE relation — must upsert, never prune the
    # persisted type like an unfiltered snapshot would.
    base = build_id_registry([
        result("list_relations", {"relations": [{"id": "r1", "character_id": "c1", "target_id": "c2", "label": "甲"}]}),
    ])
    reg = build_id_registry([
        result("list_relations", {"relations": [{"id": "r9", "character_id": "c1", "target_id": "c2", "label": "精读条"}]},
               params={"relation_id": "r9"}),
    ], persisted=base)
    assert "r1" in reg["relation"]
    assert "r9" in reg["relation"]


def test_truncated_snapshot_does_not_replace():
    base = build_id_registry([
        result("list_edges", {"edges": [{"id": "e1", "source_title": "甲", "target_title": "乙", "type": "因果"}]}),
    ])
    truncated_body = '{"edges": [{"id": "e2", "source_title": "甲", "target_title": "丙", "type": "因果"}'
    r = {"tool": "list_edges", "success": True,
         "data": '[truncated, remaining omitted]\n' + truncated_body}
    reg = build_id_registry([r], persisted=base)
    assert "e2" not in reg["edge"]
    assert label(reg, "edge", "e1") is not None


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


def test_older_unfiltered_snapshot_does_not_override_newest_filtered():
    # newest is a filtered list (non-authoritative) — an older unfiltered
    # snapshot must NOT wipe it and replace the whole type with stale data.
    reg = build_id_registry([
        result("list_relations", {"relations": [{"id": "r0", "character_id": "c1", "target_id": "c2", "label": "旧全集"}]}),
        result("list_relations", {"relations": [{"id": "r2", "character_id": "c9", "target_id": "c2", "label": "新过滤"}]},
               params={"character_id": "c9"}),
    ])
    assert "r2" in reg["relation"]
    assert "r0" not in reg["relation"]


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


def test_non_json_data_is_ignored():
    r = {"tool": "list_relations", "success": True, "data": "not json at all"}
    assert build_id_registry([r]) == {}


def test_bounds_trim_large_registry():
    results = [
        result("create_character", {"id": f"c{i}", "name": f"角色{i}"})
        for i in range(MAX_ENTRIES + 50)
    ]
    reg = build_id_registry(results)
    total = sum(len(v) for v in reg.values())
    assert total <= MAX_ENTRIES


def test_window_slides_over_old_results():
    # Results outside the trailing window (WINDOW_SIZE) are ignored.
    results = [
        result("create_character", {"id": f"c{i}", "name": f"角色{i}"})
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
        result("list_relations", {"relations": [{"id": "r1", "character_id": "c1", "target_id": "c2", "label": "甲"}]},
               params={"character_id": "c1"}),
    ])
    # filtered snapshot upserts instead of replacing
    assert "r1" in reg["relation"]


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
    assert "## 角色（2）" in out
    assert "  甲角色 | c1" in out
    assert "  乙角色 | c2" in out
    assert "（注：ID 可能已过期" in out


def test_render_empty_returns_empty_string():
    assert render_id_registry({}) == ""


def test_render_respects_char_limit():
    reg = build_id_registry([
        result("create_character", {"id": "c1", "name": "长" * 1000}),
    ])
    out = render_id_registry(reg)
    assert len(out) <= RENDER_MAX_CHARS
