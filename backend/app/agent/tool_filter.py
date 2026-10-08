"""Mode-based tool filtering — tools are independent of skills.

- ``chat`` mode: only read-only tools (read, list, search, analyze)
- ``cowriter`` mode: all tools (read + write + delete)
"""

from app.agent.tools.base import BaseTool

READ_ONLY_TOOLS: set[str] = {
    # ── 读（12）──
    "read_project", "read_acts", "read_chapters", "read_chapter_content",
    "read_chapter_package", "list_characters", "read_character",
    "list_relations", "read_relation", "list_setups", "read_progress", "search",
    # ── 范围外（4）──
    "search_knowledge", "web_search", "web_fetch", "invoke_skill",
}

COWRITER_TOOLS: set[str] = {
    # CRUD — 作品层
    "update_project", "create_project_from_material",
    # CRUD — 幕
    "create_act", "update_act", "move_act", "delete_act",
    # CRUD — 章
    "create_chapter", "update_chapter", "move_chapter", "delete_chapter",
    # CRUD — 人物
    "create_character", "update_character", "delete_character",
    # CRUD — 角色关系
    "create_relation", "update_relation", "delete_relation",
    # CRUD — 伏笔
    "create_setup", "update_setup", "delete_setup",
    # 写作侧 —— 写正文的唯一入口
    "call_writer_agent",
}

# All known tool names — used for startup consistency check.
ALL_REGISTERED_TOOLS: set[str] = READ_ONLY_TOOLS | COWRITER_TOOLS


def verify_tool_registry(registry: dict[str, BaseTool]) -> list[str]:
    """Cross-check the tool registry against the filter sets.

    Returns a list of issues (empty = all good).  Call this at startup
    to catch drift between ``tool_filter.py`` string sets and actual
    tool class ``.name`` attributes.
    """
    issues: list[str] = []
    registry_names = set(registry.keys())

    # Tools in the registry but missing from ALL_REGISTERED_TOOLS
    unlisted = registry_names - ALL_REGISTERED_TOOLS
    if unlisted:
        issues.append(
            f"Tools in registry but NOT in tool_filter.py: {sorted(unlisted)}"
        )

    # Tools in the filter sets but not in the registry
    missing = ALL_REGISTERED_TOOLS - registry_names
    if missing:
        issues.append(
            f"Tools in tool_filter.py but NOT in registry: {sorted(missing)}"
        )

    return issues


def get_available_tools(
    all_tools: dict[str, BaseTool],
    mode: str = "chat",
) -> dict[str, BaseTool]:
    """Filter tools by mode.

    Skills never gate tools — they only inject prompt guidance and
    knowledge tags.  A skill's ``tools_enabled`` / ``SKILL_TO_TOOLS``
    no longer exist.
    """
    if mode == "chat":
        return {n: t for n, t in all_tools.items() if n in READ_ONLY_TOOLS}
    return {n: t for n, t in all_tools.items() if n in READ_ONLY_TOOLS | COWRITER_TOOLS}
