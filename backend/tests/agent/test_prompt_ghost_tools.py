"""Guard: every tool name mentioned in static prompt texts (prompts/*.yaml,
loop.py teaching sections) must exist in the real tool registry.

Prevents the ghost-tool drift where prompts taught deleted tools
(read_project_overview / list_chapters / include_content=true / ...) while the
runtime registry no longer has them.
"""
import re
from pathlib import Path

from app.agent.tools import get_tool_registry

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "app" / "agent" / "prompts"
_LOOP_FILE = Path(__file__).resolve().parents[2] / "app" / "agent" / "loop.py"

# Prefixes that indicate a tool-name mention (function-calling surface).
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_])("
    r"read_|list_|write_|create_|update_|set_|search_|sync_|check_|analyze_|"
    r"suggest_|delete_|invoke_|call_|continue_|rewrite_|expand_|compress_|"
    r"fetch_|import_|export_|project_health|health_snapshot|get_|calculate_)"
    r"[a-z0-9_]*"
)
_WILDCARD = ("read_*", "list_*", "update_*", "delete_*")


def _extract_names(text: str) -> set[str]:
    names = set(_PREFIX_RE.findall(text))
    return {n for n in names if not n.endswith("_")} - set(_WILDCARD)


def _registry_names() -> set[str]:
    reg = get_tool_registry()
    return set(reg.keys())


def _assert_text_names(text: str, source: str) -> None:
    names = _extract_names(text)
    ghosts = names - _registry_names()
    assert not ghosts, f"{source}: 提示词引用了未注册的工具: {sorted(ghosts)}"


def test_prompt_yamls_reference_only_registered_tools():
    registry_extra = {"read_*"}
    yamls = sorted(_PROMPTS_DIR.glob("*.yaml"))
    assert yamls, f"no yaml files under {_PROMPTS_DIR}"
    for yaml_path in yamls:
        text = yaml_path.read_text(encoding="utf-8")
        names = _extract_names(text)
        ghosts = names - _registry_names() - registry_extra
        assert not ghosts, (
            f"{yaml_path.name}: 提示词引用了未注册的工具: {sorted(ghosts)}"
        )


def test_loop_static_teaching_references_only_registered_tools():
    text = _LOOP_FILE.read_text(encoding="utf-8")
    # Only the static teaching blocks, not arbitrary code identifiers.
    start = text.find("# --- 项目数据访问规则（必须遵守） ---")
    end = text.find("cowriter_persona = ", start)
    assert start != -1 and end != -1, "loop.py teaching block moved?"
    _assert_text_names(text[start:end], "loop.py 数据访问规则")
    guide_start = text.find("# --- 获取实体 ID 指南 ---")
    assert guide_start != -1, "loop.py ID guide moved?"
    _assert_text_names(text[guide_start:guide_start + 3000], "loop.py ID 指南")
