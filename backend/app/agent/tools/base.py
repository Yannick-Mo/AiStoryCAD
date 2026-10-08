from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.client import LLMClient


# ---------------------------------------------------------------------------
# New types for the Tool Factory Pattern + Concurrency Safety
# ---------------------------------------------------------------------------

# StrEnum 而不是 (str, Enum)：后者 str() 出来是 "ConcurrencyMode.SAFE"（3.11 的著名坑），
# 前者才是 "safe"——这个枚举是作为字符串参与比较与记录的，理应用前者。
class ConcurrencyMode(StrEnum):
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
        # 安全：缺少会话用户身份时直接拒绝，绝不静默放行（防御纵深）
        raise PermissionError("Missing user identity for project ownership check")
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
        "Act": "幕（Act）不存在，请先调用 read_acts 获取可用的幕ID",
        "Chapter": "章节（Chapter）不存在，请先调用 read_chapters（范围读取）或 read_acts 获取可用的章节ID",
        "Character": "人物（Character）不存在，请先调用 list_characters 获取可用的人物ID",
        "CharacterRelation": "角色关系（Relation）不存在，请先调用 list_relations 获取可用的关系ID",
        "Setup": "伏笔（Setup）不存在，请先调用 list_setups 获取可用的伏笔ID",
        "Project": "项目（Project）不存在，请检查项目ID是否正确",
        "Act in project": "该项目中不存在此幕ID，请用 read_acts 确认",
        "Chapter in project": "该项目中不存在此章节ID，请用 read_chapters 确认",
        "Character in project": "该项目中不存在此人物ID，请用 list_characters 确认",
        "Relation in project": "该项目中不存在此关系ID，请用 list_relations 确认",
        "Setup in project": "该项目中不存在此伏笔ID，请用 list_setups 确认",
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

    @classmethod
    def _err(cls, exc: Exception, context: str = "") -> ToolResult:
        """Translate common low-level errors into teaching-style Chinese errors.

        Centralises the UUID / permission failures that used to leak raw
        English into the next LLM turn (badly formed hexadecimal UUID string,
        "User x does not own project y" ...).
        """
        if isinstance(exc, PermissionError):
            msg = ("项目归属校验失败：该项目不属于当前用户（跨项目读取/写入被拒绝）。"
                   "请确认你操作的是自己名下且当前会话绑定的项目。")
            return ToolResult(success=False, error=msg,
                              correction_hint="下一步：切换到正确的项目后再调用本工具")
        msg = str(exc)
        low = msg.lower()
        if "uuid" in low and any(k in low for k in ("hexadecimal", "malformed", "invalid")):
            return ToolResult(
                success=False,
                error=f"传入的 ID「{msg.split(chr(39))[1] if chr(39) in msg else msg}」不是合法的 UUID——通常是 ID 拼写错误或已过期。"
                      "请先用对应的读取工具取最新 ID：幕→read_acts；章→read_chapters / read_acts；"
                      "人物→list_characters；关系→list_relations；伏笔→list_setups。"
                      "不要原样重试本工具。",
                correction_hint="下一步：运行对应读取工具获取真实 ID 后再调用本工具",
            )
        if context:
            msg = f"{context}：{msg}"
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
            "chapter_id": "必须！请先调用 read_chapters（按章号范围）或 read_acts 获取章节ID，然后立即用该ID调用本工具",
            "act_id": "必须！请先调用 read_acts 获取幕ID",
            "character_id": "必须！请先调用 list_characters 获取人物ID，然后立即用该ID调用本工具",
            "relation_id": "必须！请先调用 list_relations 获取关系ID",
            "setup_id": "必须！请先调用 list_setups 获取伏笔ID",
            "keyword": "必须提供搜索关键词（不能为空字符串）",
            "project_id": "项目ID由系统自动注入，通常无需显式传入",
            "content": "必须提供这一章的完整正文",
            "additional_content": "必须提供要接在章末的后续正文",
            "name": "必须提供名称",
            "title": "必须提供标题",
            "note": "必须提供这一条伏笔埋的是什么",
            "planted_in": "必须提供埋在哪一章（章ID，来自 read_chapters）",
            "material": "必须提供创作素材（至少10个字）",
            "project_title": "必须提供书名",
            "url": "必须提供要抓取的URL地址",
            "query": "必须提供搜索关键词或查询内容",
            "skill_name": "必须提供要启用的技能名称，见可用技能列表",
            "goal": "必须提供这一章要写什么（章蓝图）",
        }
        correction_tools = {
            "chapter_id": "read_chapters",
            "act_id": "read_acts",
            "character_id": "list_characters",
            "relation_id": "list_relations",
            "setup_id": "list_setups",
            "planted_in": "read_chapters",
        }
        hint = hints.get(key, f"请在调用工具前检查参数说明，确保提供了 {key} 参数")
        # 点名具体工具，而不是泛泛说"先使用读取工具"——模型会去猜；
        # 告诉它调哪一个，它就直接调。
        source_tool = correction_tools.get(key, "list_* 系列")
        return ToolResult(
            success=False,
            error=f"参数缺失: 工具需要 {key} 参数但未提供。{hint}。不要重复调用本工具——先执行获取 {key} 的步骤。",
            correction_hint=f"下一步：先调用 {source_tool} 获取 {key}，拿到真实 ID 后再重新调用本工具",
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

    async def on_before_run(self, args: dict, db: AsyncSession) -> None:  # noqa: B027
        """Called immediately before ``run()``.

        Can be used for logging, audit trails, or short-lived locks.

        这两个钩子是**可选**的：默认 no-op 是设计，不是漏写 @abstractmethod，
        所以带 ``# noqa: B027``（B027 的默认假设在这里不成立）。
        """
        pass

    async def on_after_run(  # noqa: B027
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
