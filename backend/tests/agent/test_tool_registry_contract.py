"""工具注册表契约（docs/故事模型设计规格.md 第 8、9 节）。

规格第 9 节的数量账原是 33 个 = 读 12 + 写 21（幕 4 + 章 4 + **内容 2** + 伏笔 3
+ 人物与关系 6 + 作品层 2）。**内容那 2 个已删除**（``write_chapter`` /
``continue_chapter``）：它们只把正文存库、不加载任何东西，走它们等于绕过
``writer.yaml`` 的整套写作方法，而且 ``write_chapter`` 能静默覆盖整章正文。
写正文现在只有 ``call_writer_agent`` 一个入口，所以本范围内是 **31** 个 = 读 12
+ 写 19。

其余不变：规格原本给伏笔的只有 create / update，``delete_setup`` 是补上的那一个
（理由见规格第 13.5 节）；**修复类 0 个**——字数实时算、顺序由实现归一化、
不变式由结构保证，所以没有"重算"和"重建"这回事（``recalc_*`` / ``relink_*`` /
``rebuild_*`` 一个都不该有）；另有 4 个属于范围外（``search_knowledge``
``web_search`` ``web_fetch`` ``invoke_skill``）、1 个属于写作侧
（``call_writer_agent``）。
"""
import re

import pytest

from app.agent.tool_filter import (
    ALL_REGISTERED_TOOLS,
    COWRITER_TOOLS,
    READ_ONLY_TOOLS,
    get_available_tools,
    verify_tool_registry,
)
from app.agent.tools import get_tool_registry


SPEC_READ = {
    "read_project", "read_acts", "read_chapters", "read_chapter_content",
    "read_chapter_package", "list_characters", "read_character",
    "list_relations", "read_relation", "list_setups", "read_progress", "search",
}

SPEC_WRITE = {
    "update_project", "create_project_from_material",
    "create_act", "update_act", "move_act", "delete_act",
    "create_chapter", "update_chapter", "move_chapter", "delete_chapter",
    "create_character", "update_character", "delete_character",
    "create_relation", "update_relation", "delete_relation",
    "create_setup", "update_setup", "delete_setup",
}

OUT_OF_SCOPE = {"search_knowledge", "web_search", "web_fetch", "invoke_skill"}
WRITER_SIDE = {"call_writer_agent"}

REPAIR_PATTERN = re.compile(r"(recalc|relink|rebuild)")

# 规格第 10 节的删除清单：这些名字不该再出现在注册表里。
DELETED_TOOLS = {
    "list_edges", "create_edge", "update_edge", "delete_edge",
    "relink_timeline", "recalc_word_counts",
    "create_scene", "update_scene", "delete_scene", "read_scene",
    "read_chapter_scenes", "expand_selection", "compress_selection",
    "read_chapter", "read_global_settings", "set_chapter_goal",
    "list_character_relations", "read_recent_chapters", "read_recent_scenes",
    "read_completed_tail", "analyze_chapter", "analyze_character_arc",
    "suggest_next", "project_health", "rewrite_scene", "continue_scene",
    "write_scene_content", "read_scene_content",
    "read_project_overview", "list_chapters", "list_scenes",
    "search_nodes", "read_recent",
}


@pytest.fixture(scope="module")
def registry():
    return get_tool_registry()


# ── 数量 ──────────────────────────────────────────────────────────────────────

def test_spec_surface_is_exactly_31_tools(registry):
    assert len(SPEC_READ) == 12
    assert len(SPEC_WRITE) == 19
    assert SPEC_READ | SPEC_WRITE <= set(registry), SPEC_READ | SPEC_WRITE - set(registry)


def test_registry_is_exactly_31_spec_tools_plus_5(registry):
    assert set(registry) == SPEC_READ | SPEC_WRITE | OUT_OF_SCOPE | WRITER_SIDE


def test_registry_size_is_36(registry):
    assert len(registry) == 36


def test_read_group_is_12_and_write_group_is_19(registry):
    assert len(SPEC_READ & set(registry)) == 12
    assert len(SPEC_WRITE & set(registry)) == 19


def test_prose_write_tools_are_not_in_the_registry(registry):
    """写正文只有 call_writer_agent 一个入口（迁移之外的一次设计删除）。

    这两个名字不许回来：它们只把正文存库、不加载任何东西，主循环走它们等于
    绕过 writer.yaml 的写作方法，而且 write_chapter 能静默覆盖整章正文。
    """
    for gone in ("write_chapter", "continue_chapter"):
        assert gone not in registry, gone
    assert "call_writer_agent" in registry


# ── 修复类必须为 0 ────────────────────────────────────────────────────────────

def test_no_tool_is_a_repair_tool(registry):
    offenders = sorted(name for name in registry if REPAIR_PATTERN.search(name))
    assert not offenders, f"修复类工具必须为 0 个：{offenders}"


def test_no_repair_categories_in_the_source(registry):
    """注册表里不该残留任何 recalc/relink/rebuild 的类。"""
    sources = [type(tool).__name__ for tool in registry.values()]
    offenders = [name for name in sources if REPAIR_PATTERN.search(name.lower())]
    assert not offenders, offenders


# ── 删除清单 ──────────────────────────────────────────────────────────────────

def test_deleted_tools_are_gone(registry):
    leaked = sorted(DELETED_TOOLS & set(registry))
    assert not leaked, f"已删除的工具又出现了：{leaked}"


def test_scene_and_edge_tools_are_gone(registry):
    """按名字分段判定，免得 search_knowledge 里的 "edge" 被误判。"""
    for needle in ("scene", "edge"):
        leaked = sorted(
            name for name in registry if needle in name.split("_")
        )
        assert not leaked, f"{needle} 层已删除，不该有这些工具：{leaked}"


# ── filter 集合与注册表一致 ───────────────────────────────────────────────────

def test_verify_tool_registry_reports_no_issues(registry):
    assert verify_tool_registry(registry) == []


def test_filter_sets_match_the_spec_groups():
    assert READ_ONLY_TOOLS == SPEC_READ | OUT_OF_SCOPE
    assert COWRITER_TOOLS == SPEC_WRITE | WRITER_SIDE
    assert ALL_REGISTERED_TOOLS == SPEC_READ | SPEC_WRITE | OUT_OF_SCOPE | WRITER_SIDE
    assert not (READ_ONLY_TOOLS & COWRITER_TOOLS)


def test_chat_mode_exposes_read_tools_only(registry):
    chat = get_available_tools(registry, mode="chat")
    assert set(chat) == SPEC_READ | OUT_OF_SCOPE
    assert not (set(chat) & SPEC_WRITE)


def test_cowriter_mode_exposes_everything(registry):
    cowriter = get_available_tools(registry, mode="cowriter")
    assert set(cowriter) == set(registry)


# ── 读工具只读、写工具是写操作 ────────────────────────────────────────────────

def test_every_read_tool_is_read_only(registry):
    for name in SPEC_READ | OUT_OF_SCOPE:
        assert not registry[name].is_write_operation, name


def test_every_write_tool_is_a_write_operation(registry):
    for name in SPEC_WRITE:
        assert registry[name].is_write_operation, name


def test_list_tools_are_read_only_and_never_write(registry):
    """名录类工具是导航地图：永远只读，否则模型会把"查 ID"和"改数据"混起来。"""
    list_tools = {n for n in registry if n.startswith("list_")}
    assert list_tools == {"list_characters", "list_relations", "list_setups"}
    for name in list_tools:
        assert not registry[name].is_write_operation, name
        assert not registry[name].meta.is_destructive, name
        assert not registry[name].meta.needs_confirmation, name


def test_destructive_tools_need_confirmation(registry):
    """删除类工具既标破坏性、又要求确认——不能悄悄删掉一章正文。

    ``write_chapter`` 曾经只标了 ``is_destructive``、没要求确认，于是模型选错路时
    能静默覆盖整章正文。它现在整个删掉了（写正文只有 call_writer_agent 一个入口），
    所以这里不用再替它操心。
    """
    for name in ("delete_act", "delete_chapter", "delete_character",
                 "delete_relation", "delete_setup"):
        tool = registry[name]
        assert tool.meta.is_destructive, name
        assert tool.meta.needs_confirmation, name


# ── schema 形状 ───────────────────────────────────────────────────────────────

def test_every_tool_exposes_a_valid_object_schema(registry):
    for name, tool in registry.items():
        schema = tool.meta.parameters
        assert schema.get("type") == "object", name
        assert isinstance(schema.get("properties", {}), dict), name
        for required in schema.get("required", []):
            assert required in schema["properties"], f"{name}: required {required} 没在 properties 里"


def test_every_tool_has_a_name_matching_its_registry_key(registry):
    for name, tool in registry.items():
        assert tool._effective_name == name


def test_openai_schema_serialisation_closes_the_object(registry):
    for name, tool in registry.items():
        fn = tool.to_openai_tool()["function"]
        assert fn["name"] == name
        assert fn["parameters"]["additionalProperties"] is False
