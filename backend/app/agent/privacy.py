"""Privacy / display-name sanitisation for tool-related SSE events.

All internal tool function names, parameters, and error details are
mapped to user-facing Chinese labels before they leave the server.
This is the last filter before data goes to ``routes_ai_v2.py``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Display-name mapping ────────────────────────────────────────────────
# Every tool function name the AI assistant may call is mapped to a
# user-facing Chinese label.  Missing names fall back to a generic label.

TOOL_DISPLAY_NAMES: dict[str, str] = {
    # Project read
    "read_project": "读取项目",
    "read_global_settings": "读取全局设定",
    "read_chapter": "读取章节",
    "read_chapter_scenes": "读取章内场景清单",
    "read_scene": "读取场景蓝图",
    "read_scene_content": "读取场景正文",
    "read_chapters": "范围读取章节",
    "read_recent_scenes": "读取最近场景",
    "read_recent_chapters": "读取最近章节",
    "read_character": "读取角色",
    "read_relation": "精读关系",
    "list_characters": "列出角色",
    "list_character_relations": "列出角色关系网",
    "list_relations": "列出关系",
    "list_edges": "列出关联",
    "search_nodes": "搜索节点",
    # Project write
    "create_scene": "创建场景",
    "update_scene": "修改场景",
    "delete_scene": "删除场景",
    "create_chapter": "创建章节",
    "update_chapter": "修改章节",
    "delete_chapter": "删除章节",
    "create_act": "创建幕",
    "update_act": "修改幕",
    "delete_act": "删除幕",
    "update_project": "更新项目",
    "set_chapter_goal": "设定章节目标",
    "create_project_from_material": "从素材创建项目",
    # Character
    "create_character": "创建角色",
    "update_character": "修改角色",
    "delete_character": "删除角色",
    "delete_relation": "删除关系",
    "update_relation": "修改关系",
    # Edge
    "create_edge": "创建关联",
    "update_edge": "修改关联",
    "delete_edge": "删除关联",
    # Agents
    "call_writer_agent": "调用写作智能体",
    # Analysis
    "check_consistency": "检查一致性",
    "analyze_chapter": "分析章节",
    "analyze_character_arc": "分析角色弧",
    "suggest_next": "建议下一步",
    "project_health": "项目健康检查",
    # Writing
    "write_scene_content": "写入场景内容",
    "continue_scene": "续写场景",
    "rewrite_scene": "重写场景",
    "expand_selection": "展开选中内容",
    "compress_selection": "压缩选中内容",
    "sync_scene_blueprint": "同步场景蓝图",
    # Knowledge / Web
    "search_knowledge": "搜索知识库",
    "web_search": "联网搜索",
    "web_fetch": "读取网页",
    # Internal / plans
    "cowriter_analysis": "内容分析",
    "plan_tools": "执行计划",
}

# Patterns to strip internal function names from error strings.
# The LLM-assisted recovery path may embed "Tool 'xxx' failed" or
# similar patterns in messages sent to the front end.
_ERROR_SANITISE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Tool '([^']+)'"), "操作"),
    (re.compile(r"tool '([^']+)'"), "操作"),
    (re.compile(r"(?i)(function|method|endpoint) '[^']+'"), "接口"),
]


def _sanitise_error_text(text: str) -> str:
    """Remove internal function names from an error message."""
    result = text
    for pattern, replacement in _ERROR_SANITISE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


# ── Client-bound error deep sanitisation ──────────────────────────────
# 异常文本可能携带令牌/API Key/SQL 文件路径,发送给客户端前必须抹除。

_REDACT_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+[a-zA-Z0-9._\-]+|"
    r"authorization\s*[:=]\s*[^\s,;]+|"
    r"api[_-]?key\s*[:=]\s*[^\s,;]+|"
    r"sk-[a-zA-Z0-9_\-]{8,})"
)
_REDACT_PATH_RE = re.compile(
    r"/(?:home|app|Users|var|tmp)(?:/[^\s\"',;)\]}>]+)+"
)


def sanitise_error_text_for_client(text: str) -> str:
    """深度净化即将发给客户端的错误文本。

    抹除 Bearer/Authorization/API Key/sk- 密钥片段,隐藏 /home、/app、
    /Users、/var、/tmp 绝对路径,压缩换行并截断到 300 字符,最后复用
    _sanitise_error_text 做内部函数名映射。完整错误由服务端日志记录。
    """
    if not text:
        return text
    text = _REDACT_SECRET_RE.sub("[REDACTED]", text)
    text = _REDACT_PATH_RE.sub("[路径已隐藏]", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text[:300]
    return _sanitise_error_text(text)


def _short_user_error(text: str) -> str:
    """Shorten a technical error to a brief user-facing message.

    The full error is logged separately; the frontend only sees this
    short hint so it doesn't confuse the user with DB/SQL details.
    """
    if not text:
        return ""
    # Map known long patterns to short messages
    if "InFailedSQLTransactionError" in text or "current transaction is aborted" in text:
        return "数据库异常"
    if "timeout" in text.lower() or "timed out" in text:
        return "操作超时"
    if "not found" in text.lower():
        return "数据不存在"
    if "not authorized" in text.lower() or "permission" in text.lower() or "denied" in text.lower():
        return "权限不足"
    if "connection" in text.lower() and ("refused" in text.lower() or "reset" in text.lower()):
        return "连接失败"
    if "duplicate" in text.lower() or "already exists" in text.lower():
        return "数据重复"
    # Default: keep short
    text = text.strip().rstrip(".")
    if len(text) > 40:
        return text[:37] + "..."
    return text


def _display_name(internal: str) -> str:
    """Return the user-facing label for an internal tool name."""
    return TOOL_DISPLAY_NAMES.get(internal, "执行操作")


# ── Event sanitizers ────────────────────────────────────────────────────


def _sanitise_tool_done(data: dict[str, Any]) -> dict[str, Any]:
    """Replace the internal tool name with its display label, strip
    internal fields, and shorten error messages for the frontend.

    The full error is logged server-side; the AI assistant receives
    the full error via the ``tool`` role message built in ``loop.py``.
    """
    result = dict(data)
    internal = result.get("tool", "")
    result["tool"] = _display_name(internal)
    result.pop("_tool_use_id", None)
    if result.get("error"):
        full = result["error"]
        logger.warning("Tool '%s' error: %s", internal, full)
        result["error"] = _short_user_error(full)
    return result


def _sanitise_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Sanitise every step's internal tool name and strip raw params."""
    steps = plan.get("steps", [])
    clean_steps = []
    for step in steps:
        internal = step.get("tool", "")
        desc = step.get("description", "")
        # If description fell back to the raw internal name, replace it
        if desc == internal:
            desc = _display_name(internal)
        clean_steps.append({
            "tool": _display_name(internal),
            "description": desc,
        })
    result = dict(plan)
    result["steps"] = clean_steps
    if "reasoning" in result:
        result["reasoning"] = _sanitise_error_text(result["reasoning"])
    return result


def _sanitise_project_updated(data: dict[str, Any]) -> dict[str, Any]:
    """Replace tool names and remove details that leak param names."""
    result = dict(data)
    result["tools_executed"] = [
        _display_name(t) for t in result.get("tools_executed", [])
    ]
    details = []
    for td in result.get("tool_details", []):
        details.append({"name": _display_name(td.get("name", ""))})
    result["tool_details"] = details
    return result


# ── Main entry ──────────────────────────────────────────────────────────


def sanitise_event(event_type: str, data_raw: str) -> str:
    """Sanitise an SSE event's data payload before sending to the client.

    Args:
        event_type:  SSE event type (``tool_done``, ``plan``, etc.)
        data_raw:    Raw string payload (JSON or plain text).

    Returns:
        Sanitised payload string.  Non‑tool events pass through unchanged.
    """
    if event_type == "tool_done":
        parsed = json.loads(data_raw)
        return json.dumps(_sanitise_tool_done(parsed), ensure_ascii=False)
    if event_type == "plan":
        try:
            parsed = json.loads(data_raw)
            return json.dumps(_sanitise_plan(parsed), ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return data_raw
    if event_type == "project_updated":
        try:
            parsed = json.loads(data_raw)
            return json.dumps(_sanitise_project_updated(parsed), ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return data_raw
    if event_type == "error":
        try:
            parsed = json.loads(data_raw)
            if isinstance(parsed, dict):
                return json.dumps(
                    {k: sanitise_error_text_for_client(v) if isinstance(v, str) else v
                     for k, v in parsed.items()},
                    ensure_ascii=False,
                )
        except (json.JSONDecodeError, TypeError):
            pass
        return sanitise_error_text_for_client(data_raw)
    return data_raw
