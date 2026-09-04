from __future__ import annotations

from app.llm.client import LLMClient
from .base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode


def _safe_instantiate(cls: type[BaseTool], llm_client: LLMClient | None = None) -> BaseTool | None:
    try:
        return cls(llm_client=llm_client)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Failed to instantiate tool %s", cls.__name__, exc_info=True)
        return None


def get_tool_registry(llm_client: LLMClient | None = None) -> dict[str, BaseTool]:
    from .list_tools import (
        ListRelationsTool, ListEdgesTool, SearchNodesTool,
    )
    from .range_tools import ReadChaptersTool, ReadRecentTool
    from .project_tools import (
        ReadProjectTool, ReadChapterTool, ReadSceneTool, CreateSceneTool, UpdateSceneTool,
        SetChapterGoalTool, UpdateChapterTool, UpdateActTool,
    )
    from .character_tools import (
        ListCharactersTool, ReadCharacterTool, CreateCharacterTool, UpdateCharacterTool,
        CreateRelationTool, UpdateRelationTool, DeleteCharacterTool, DeleteRelationTool,
    )
    from .analysis_tools import ConsistencyCheckTool
    from .analysis_v2_tools import AnalyzeChapterTool, AnalyzeCharacterArcTool, SuggestNextTool, ProjectHealthTool
    from .writing_tools import WriteSceneContentTool, ContinueSceneTool, RewriteSceneTool, ExpandSelectionTool, CompressSelectionTool
    from .knowledge_tools import SearchKnowledgeTool
    from .web_search import WebSearchTool
    from .web_fetch import WebFetchTool
    from .project_admin_tools import (
        CreateActTool, CreateChapterTool, UpdateProjectTool,
        DeleteSceneTool, DeleteChapterTool, DeleteActTool,
        CreateProjectFromMaterialTool,
        CreateEdgeTool, UpdateEdgeTool, DeleteEdgeTool,
    )
    from .skill_tool import InvokeSkillTool
    from .writer_tool import CallWriterAgentTool, SyncSceneBlueprintTool
    from .word_count_tools import RecalcWordCountsTool
    classes = [
        ListRelationsTool, ListEdgesTool, SearchNodesTool,
        ReadChaptersTool, ReadRecentTool,
        ReadProjectTool, ReadChapterTool, ReadSceneTool, CreateSceneTool, UpdateSceneTool,
        SetChapterGoalTool, UpdateChapterTool, UpdateActTool,
        ListCharactersTool, ReadCharacterTool, CreateCharacterTool, UpdateCharacterTool,
        CreateRelationTool, UpdateRelationTool, DeleteCharacterTool, DeleteRelationTool,
        ConsistencyCheckTool,
        AnalyzeChapterTool, AnalyzeCharacterArcTool, SuggestNextTool, ProjectHealthTool,
        WriteSceneContentTool, ContinueSceneTool, RewriteSceneTool, ExpandSelectionTool, CompressSelectionTool,
        SearchKnowledgeTool,
        WebSearchTool, WebFetchTool,
        CreateActTool, CreateChapterTool, UpdateProjectTool,
        DeleteSceneTool, DeleteChapterTool, DeleteActTool,
        CreateProjectFromMaterialTool,
        CreateEdgeTool, UpdateEdgeTool, DeleteEdgeTool,
        InvokeSkillTool,
        CallWriterAgentTool,
        SyncSceneBlueprintTool,
        RecalcWordCountsTool,
    ]
    registry: dict[str, BaseTool] = {}
    for cls in classes:
        tool_name = cls.meta.name if cls.meta is not None else getattr(cls, "name", "")
        if tool_name in registry:
            raise ValueError(f"Duplicate tool name: {tool_name}")
        inst = _safe_instantiate(cls, llm_client)
        if inst is not None:
            registry[tool_name] = inst
    return registry


def get_filtered_tools(
    all_tools: dict[str, BaseTool],
    mode: str = "chat",
) -> dict[str, BaseTool]:
    from app.agent.tool_filter import get_available_tools
    return get_available_tools(all_tools, mode=mode)


def get_tool_descriptions(tools: dict[str, BaseTool]) -> str:
    """Build a compact tool reference string for the system prompt.

    Includes tool name, description, destructive/confirmation markers, and
    **required parameter hints** with ID source annotations so models
    (especially DeepSeek flash variants) can see how to obtain required IDs.
    """
    import re
    lines: list[str] = []
    lines.append(
        "# (必须: <param>) = 必需参数。← tool_name = 先调此工具获取值再传入。\n"
        "# [需确认] = 需用户批准  [破坏性] = 不可逆\n"
        "# # --- 已知实体 ID --- 段会列出近期出现的实体 ID，可直接引用。\n"
    )
    for t_name, t_inst in sorted(tools.items()):
        d = t_inst.to_openai_tool()
        fn = d.get("function", {})

        destructive_str = ""
        if hasattr(t_inst, "meta") and t_inst.meta is not None:
            if t_inst.meta.needs_confirmation:
                destructive_str = " [需确认]"
            elif t_inst.meta.is_destructive:
                destructive_str = " [破坏性]"

        # Show ALL parameter names in parentheses after the tool name,
        # plus list required params explicitly.
        params_schema = fn.get("parameters", {})
        properties = params_schema.get("properties", {})
        required = params_schema.get("required", [])
        param_names = list(properties.keys())
        param_hint = ""
        if param_names:
            param_hint = "(" + ", ".join(param_names) + ")"
        req_parts: list[str] = []
        for p in required:
            prop = properties.get(p, {})
            desc = prop.get("description", "")
            m = re.search(r'来自\s+(\S+)', desc)
            if m:
                req_parts.append(f"{p} ← {m.group(1)}")
            else:
                req_parts.append(p)
        if req_parts:
            req_hint = " [必须: " + ", ".join(req_parts) + "]"
        else:
            req_hint = ""

        lines.append(f"- {t_name}{param_hint}: {fn.get('description', '')}{req_hint}{destructive_str}")
    return "\n".join(lines)


__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolMeta",
    "ConcurrencyMode",
    "get_tool_registry",
    "get_filtered_tools",
    "get_tool_descriptions",
]
