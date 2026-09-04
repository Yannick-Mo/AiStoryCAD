"""Mode-based tool filtering — tools are independent of skills.

- ``chat`` mode: only read-only tools (read, list, search, analyze)
- ``cowriter`` mode: all tools (read + write + delete)
"""

from app.agent.tools.base import BaseTool

READ_ONLY_TOOLS: set[str] = {
    "read_project", "read_chapter", "read_scene", "read_character",
    "search_knowledge",
    "analyze_chapter", "analyze_character_arc", "project_health",
    "check_consistency", "suggest_next",
    "read_chapters", "read_recent",
    "list_characters",
    "list_relations", "list_edges", "search_nodes",
    "web_search", "web_fetch",
    "invoke_skill",
}

COWRITER_TOOLS: set[str] = {
    # CRUD — project
    "update_project", "create_project_from_material",
    # CRUD — act
    "create_act", "update_act", "delete_act",
    # CRUD — chapter
    "create_chapter", "update_chapter", "delete_chapter",
    "set_chapter_goal",
    # CRUD — scene
    "create_scene", "update_scene", "delete_scene",
    # CRUD — character
    "create_character", "update_character", "delete_character",
    "create_relation", "update_relation", "delete_relation",
    # CRUD — edge
    "create_edge", "update_edge", "delete_edge",
    # Writing
    "write_scene_content", "continue_scene", "rewrite_scene",
    "expand_selection", "compress_selection",
    "sync_scene_blueprint",
    # Word count
    "recalc_word_counts",
    # Agents
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
