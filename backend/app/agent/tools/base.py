from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.client import LLMClient


# ---------------------------------------------------------------------------
# New types for the Tool Factory Pattern + Concurrency Safety
# ---------------------------------------------------------------------------

class ConcurrencyMode(str, Enum):
    """Controls how a tool interacts with concurrent executions."""
    SAFE = "safe"          # Can run in parallel with other SAFE tools
    EXCLUSIVE = "exclusive"  # Must run alone (write operations)
    BARRIER = "barrier"    # Must wait for all others to complete first


@dataclass
class ToolMeta:
    """Immutable metadata describing a tool's capabilities and constraints."""
    name: str
    description: str
    parameters: dict = field(default_factory=dict)
    concurrency: ConcurrencyMode = ConcurrencyMode.EXCLUSIVE
    is_destructive: bool = False  # Irreversible operations (delete, overwrite, send)
    needs_confirmation: bool = False  # Must get user approval before execution
    timeout: int = 30  # Seconds
    max_result_chars: int = 8000  # Truncate results beyond this


# ---------------------------------------------------------------------------
# ToolResult (unchanged)
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    success: bool = True
    data: Any = None
    error: str | None = None
    correction_hint: str | None = None  # LLM self-correction guidance

    def to_dict(self) -> dict:
        result = {"success": self.success, "data": self.data, "error": self.error}
        if self.correction_hint:
            result["correction_hint"] = self.correction_hint
        return result


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

async def verify_project_owner(
    db: AsyncSession, project_id, user_id: str | None
) -> None:
    if user_id is None:
        return
    from app.project.models import Project
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.owner_id == user_id)
    )
    if not result.scalar_one_or_none():
        raise PermissionError(
            f"User {user_id} does not own project {project_id}"
        )


# ---------------------------------------------------------------------------
# BaseTool
# ---------------------------------------------------------------------------

class BaseTool(ABC):
    """Abstract base for every agent tool.

    Each subclass MUST set ``meta`` to a :class:`ToolMeta` instance
    containing name, description, parameters, concurrency, etc.

    Hook methods (validate_input, on_before_run, on_after_run, inputs_equivalent)
    can be overridden by subclasses to control execution lifecycle.
    """

    meta: ToolMeta | None = None
    llm_client: LLMClient | None = None
    _uses_own_session: bool = False

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # Property accessors — all driven by meta
    # ------------------------------------------------------------------

    @property
    def _effective_name(self) -> str:
        return self.meta.name if self.meta is not None else ""

    @property
    def _effective_description(self) -> str:
        return self.meta.description if self.meta is not None else ""

    @property
    def _effective_parameters(self) -> dict:
        return self.meta.parameters if self.meta is not None else {}

    @property
    def _effective_concurrency(self) -> ConcurrencyMode:
        if self.meta is not None:
            return self.meta.concurrency
        return ConcurrencyMode.SAFE

    @property
    def is_write_operation(self) -> bool:
        """True when this tool modifies data (EXCLUSIVE concurrency)."""
        return self._effective_concurrency == ConcurrencyMode.EXCLUSIVE

    @property
    def _effective_is_destructive(self) -> bool:
        return self.meta.is_destructive if self.meta is not None else False

    @property
    def _effective_timeout(self) -> int:
        if self.meta is not None:
            return self.meta.timeout
        return 30

    @property
    def _effective_max_result_chars(self) -> int:
        if self.meta is not None:
            return self.meta.max_result_chars
        return 8000

    # ------------------------------------------------------------------
    # Parameter validation helper
    # ------------------------------------------------------------------

    # Centralised entity-not-found messages so every tool returns
    # consistent Chinese-language errors with self-correction guidance.
    ENTITY_NOT_FOUND = {
        "Act": "幕（Act）不存在，请先调用 read_full_project 或查看项目结构概览确认幕ID",
        "Chapter": "章节（Chapter）不存在，请先调用 list_chapters 获取可用的章节ID",
        "Scene": "场景（Scene）不存在，请先调用 list_scenes 获取可用的场景ID",
        "SceneContent": "场景正文（SceneContent）不存在，请先调用 write_scene_content 写入内容",
        "Character": "角色（Character）不存在，请先调用 list_characters 获取可用的角色ID",
        "CharacterRelation": "角色关系（Relation）不存在，请先调用 list_relations 获取可用的关系ID",
        "ChapterEdge": "章节连线（Edge）不存在，请先调用 list_edges 获取可用的连线ID",
        "Theme": "主题（Theme）不存在，请先查看项目结构概览中的主题列表",
        "Project": "项目（Project）不存在，请检查项目ID是否正确",
        "Act in project": "该项目中不存在此幕ID，请查看项目结构概览确认",
        "Chapter in project": "该项目中不存在此章节ID，请调用 list_chapters 确认",
        "Scene in project": "该项目中不存在此场景ID，请调用 list_scenes 确认",
        "Character in project": "该项目中不存在此角色ID，请调用 list_characters 确认",
        "Edge in project": "该项目中不存在此连线ID，请调用 list_edges 确认",
        "Theme in project": "该项目中不存在此主题ID",
        "Relation in project": "该项目中不存在此关系ID，请调用 list_relations 确认",
    }

    # ── Structured error helpers for common failure modes ──────────

    @classmethod
    def _not_found(cls, entity: str, extra: str = "") -> ToolResult:
        msg = cls.ENTITY_NOT_FOUND.get(entity, f"{entity} 不存在")
        if extra:
            msg = f"{msg}。{extra}"
        return ToolResult(success=False, error=msg, correction_hint=f"请先使用读取工具确认可用的{entity}ID后再重试")

    @classmethod
    def _already_exists(cls, entity: str, name: str, existing_id: str = "") -> ToolResult:
        hint = f"请使用更新工具修改已有{entity}，或更换名称重新创建"
        if existing_id:
            hint = f"已存在同名{entity}（ID: {existing_id}）。{hint}"
        return ToolResult(
            success=False,
            error=f"已存在同名{entity}：'{name}'",
            correction_hint=hint,
        )

    @classmethod
    def _permission_denied(cls, entity: str = "") -> ToolResult:
        msg = f"权限不足：{entity} 不属于当前用户" if entity else "权限不足：不属于当前用户"
        return ToolResult(success=False, error=msg)

    @staticmethod
    def _require_param(kwargs: dict, key: str, hint: str = "") -> str | None:
        """Get a required param from kwargs, returning None if missing.

        The caller should check for None and return a clear ToolResult error.
        """
        val = kwargs.get(key)
        if val is None:
            return None
        if isinstance(val, str):
            val = val.strip()
            if not val:
                return None
        return val

    @staticmethod
    def _missing_param(key: str) -> ToolResult:
        """Return a ToolResult for a missing required parameter.

        The error message includes a STOP signal to prevent the LLM from
        retrying the same tool without fixing the parameter — a common
        failure mode with DeepSeek flash models.
        """
        hints = {
            "chapter_id": "必须！请先调用 list_chapters 获取章节ID，然后立即用该ID调用本工具",
            "scene_id": "必须！请先调用 list_scenes 获取场景ID，然后立即用该ID调用本工具",
            "character_id": "必须！请先调用 list_characters 获取角色ID，然后立即用该ID调用本工具",
            "act_id": "必须！请先调用 list_chapters 或查看项目结构概览获取幕ID",
            "keyword": "必须提供搜索关键词（不能为空字符串）",
            "project_id": "项目ID由系统自动注入，通常无需显式传入",
            "edge_id": "必须！请先调用 list_edges 获取连线ID",
            "relation_id": "必须！请先调用 list_relations 获取关系ID",
            "theme_id": "必须！请先查看项目结构概览中的主题列表获取主题ID",
            "source_id": "必须！请先调用 list_chapters 获取源章节ID",
            "target_id": "必须！请先调用 list_chapters 获取目标章节ID",
            "content": "必须提供场景正文内容",
            "additional_content": "必须提供续写的内容",
            "name": "必须提供名称",
            "title": "必须提供标题",
            "material": "必须提供创作素材（至少10个字）",
            "project_title": "必须提供项目标题",
            "url": "必须提供要抓取的URL地址",
            "query": "必须提供搜索关键词或查询内容",
            "skill_name": "必须提供要启用的技能名称，见可用技能列表",
            "goal": "必须提供章节写作目标",
            "original_text": "必须提供原始文本（用于定位要替换的段落）",
            "expanded_text": "必须提供扩写后的完整段落",
            "compressed_text": "必须提供压缩后的文本",
            "style": "必须提供重写风格说明",
            "edge_type": "必须提供连线类型（timeline/causal/foreshadow/character/theme）",
        }
        correction_tools = {
            "chapter_id": "list_chapters",
            "scene_id": "list_scenes",
            "character_id": "list_characters",
            "act_id": "list_chapters",
            "edge_id": "list_edges",
            "relation_id": "list_relations",
            "source_id": "list_chapters",
            "target_id": "list_chapters",
        }
        hint = hints.get(key, f"请在调用工具前检查参数说明，确保提供了 {key} 参数")
        source_tool = correction_tools.get(key, "list_* 系列")
        return ToolResult(
            success=False,
            error=f"参数缺失: 工具需要 {key} 参数但未提供。{hint}。不要重复调用本工具——先执行获取 {key} 的步骤。",
            correction_hint=f"下一步：先使用读取工具获取 {key}，拿到后再重新调用本工具",
        )

    # ------------------------------------------------------------------
    # Core abstract method
    # ------------------------------------------------------------------

    @abstractmethod
    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        """Execute the tool.  Subclasses MUST implement this."""
        ...

    # ------------------------------------------------------------------
    # Hook methods (override in subclasses)
    # ------------------------------------------------------------------

    async def validate_input(self, args: dict) -> tuple[bool, str]:
        """Validate input arguments BEFORE execution.

        Returns ``(True, "")`` when valid, or ``(False, "<reason>")`` otherwise.
        The default implementation always passes.
        """
        return True, ""

    async def on_before_run(self, args: dict, db: AsyncSession) -> None:
        """Called immediately before ``run()``.

        Can be used for logging, audit trails, or short-lived locks.
        """
        pass

    async def on_after_run(
        self, result: ToolResult, args: dict, db: AsyncSession
    ) -> None:
        """Called immediately after ``run()`` (even on failure).

        Can be used for cleanup, cache invalidation, or metric emission.
        """
        pass

    def inputs_equivalent(self, a: dict, b: dict) -> bool:
        """Return True when two sets of arguments are considered identical.

        Used by deduplication logic (e.g. the plan node).  The default
        returns ``False`` (never deduplicate) — subclasses should override
        for idempotent tools.
        """
        return False

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_openai_tool(self) -> dict:
        params = self._effective_parameters
        if isinstance(params, dict) and "additionalProperties" not in params:
            params = {**params, "additionalProperties": False}
        return {
            "type": "function",
            "function": {
                "name": self._effective_name,
                "description": self._effective_description,
                "parameters": params,
            },
        }
