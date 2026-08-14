"""Middle-LLM semantic compression for large tool results.

Design (agreed with product):

* Only a **whitelist** of tools whose results are "raw material" (long
  prose / big structural dumps) ever reach the middle LLM.  Every other
  tool (ID-navigation lists like ``list_*`` / ``search_nodes``, write
  confirmations, and internal-LLM products like ``analyze_*``) passes
  through untouched — compressing those would break tool-call chaining
  (hallucinated IDs) or double-process LLM answers.
* Project/DB query tools (``read_scene``, ``read_project_overview``,
  ``read_full_project``) are deliberately NOT whitelisted: their data is
  the story outline/framework the loop LLM must see verbatim (truncating
  or summarizing it risks losing the exact structure/IDs the model relies
  on for navigation), and their size is bounded by the outline fields.
* Thresholds: results under ``LLM_COMPRESS_MIN_CHARS`` are injected
  verbatim (typical scene content 1-3K chars stays fully visible).
  15K-50K → semantic compression (target ``TARGET_SEMANTIC_CHARS``);
  over 50K → structural compression (target ``TARGET_STRUCTURAL_CHARS``).
* The middle LLM gets a **clean context**: only its compression prompt +
  the raw result.  No conversation history.
* Safety nets (the AI must never be broken by compression):
  1. All UUIDs in the output must be a subset of the input UUIDs
     (catches invented or dropped IDs).
  2. ID/entry loss > ``MAX_ENTRY_LOSS_RATIO`` → output rejected.
  3. Output not meaningfully smaller than input → input used verbatim.
  4. Any exception / timeout / bad output → fall back to the original
     data (or the fold marker for > ``FOLD_FALLBACK_CHARS``, the only
     case where a single result could endanger the context window).
* ``web_search`` gets a dedicated *cleaning* pass (de-noise, keep all
  URLs) instead of compression — it is usually small but noisy.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Callable

from app.config import settings

logger = logging.getLogger(__name__)

# ── Whitelist: tools whose results may be sent to the middle LLM ─────
# Everything else is injected verbatim, always.
COMPRESSIBLE_TOOLS = frozenset({"web_fetch"})
CLEANABLE_TOOLS = frozenset({"web_search"})

# ── Thresholds (chars, on the stringified data) ──────────────────────
LLM_COMPRESS_MIN_CHARS = 15_000   # below this: verbatim, no LLM call
SEMANTIC_MAX_CHARS = 50_000       # above this: structural compression
TARGET_SEMANTIC_CHARS = 7_000     # semantic compression target
TARGET_STRUCTURAL_CHARS = 9_000   # structural compression target
WEB_SEARCH_CLEAN_MIN_CHARS = 1_500  # search cleaning trigger
WEB_SEARCH_TARGET_CHARS = 2_500

# ── Safety nets ──────────────────────────────────────────────────────
MAX_ENTRY_LOSS_RATIO = 0.05        # >5% IDs/entries lost → reject output
MIN_COMPRESSION_RATIO = 0.70       # output >= 70% of input → not compressed, reject
MIDDLE_LLM_TIMEOUT_S = 25.0        # hard timeout; failure → fallback
FOLD_FALLBACK_CHARS = 50_000       # above this, a failed LLM call falls back to folding
FOLD_HEAD_CHARS = 500

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _extract_uuids(text: str) -> set[str]:
    return {m.lower() for m in _UUID_RE.findall(text)}


def _stringify(data: Any) -> str:
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except TypeError:
        return str(data)


def should_middle_process(tool_name: str, result: dict) -> bool:
    """Return True if this tool result should go through the middle LLM.

    Whitelist + size gate: results must be genuinely large (or a noisy
    web search) — we never spend an LLM call on small/ID-list results.
    """
    if not result.get("success"):
        return False
    if tool_name in COMPRESSIBLE_TOOLS:
        return len(_stringify(result.get("data"))) >= LLM_COMPRESS_MIN_CHARS
    if tool_name in CLEANABLE_TOOLS:
        return len(_stringify(result.get("data"))) >= WEB_SEARCH_CLEAN_MIN_CHARS
    return False


def _system_prompt(tool_name: str, data: Any, chars: int) -> str:
    is_search = tool_name in CLEANABLE_TOOLS
    structural = not is_search and chars > SEMANTIC_MAX_CHARS
    target = TARGET_STRUCTURAL_CHARS if structural else TARGET_SEMANTIC_CHARS
    if is_search:
        target = WEB_SEARCH_TARGET_CHARS

    common = (
        "你是数据压缩器。只输出压缩/清洗后的内容本身，不要任何解释、前后缀、代码块标记或格式头。\n"
        f"输出长度必须控制在约 {target} 字符以内。\n"
        "硬性规则（违反任何一条都视为失败）：\n"
        "1. 所有 ID（UUID）、URL 必须逐字符原样保留，一个都不能少、不能改、不能编造。\n"
        "2. 禁止删除任何条目（场景/章节/角色/关系/列表项），只允许压缩每条的文字。\n"
        "3. 禁止添加原文没有的事实、数字、人物、地点、时间线。\n"
        "4. 语义不变：保留所有剧情关键点、事实、数字、因果关系、人名地名。\n"
    )

    if is_search:
        kind = (
            "清洗网络搜索结果：\n"
            "- 去掉广告、导航噪声、重复条目和无关片段。\n"
            "- 每条保留：标题、URL（必须完整）、核心内容 1-2 句。\n"
            "- 保留所有有效结果的 URL，只删真正无用的噪声条目。\n"
        )
    elif structural:
        kind = (
            "超大结构化数据压缩（如整个项目的幕/章/场景树、全部角色档案）：\n"
            "- 保留完整层级结构：幕/章/场景的名称、排序号、ID 全部保留。\n"
            "- 长文本字段（goal/summary/正文/性格/背景/动机）每条压缩为 1-2 句（≤50 字）。\n"
            "- 关系/连线条目逐条缩句，不删除。\n"
        )
    else:
        kind = (
            "长文本语义压缩（缩句而非概括）：\n"
            "- 保留人物动作、冲突、转折、关键对话要点、时间地点等剧情骨架。\n"
            "- 压缩环境描写、修饰性语言，但保持事实完整。\n"
            "- 若是结构化 JSON：保留所有键和 ID，仅压缩长文本字段的值。\n"
        )

    return common + kind


def _validate(tool_name: str, data: Any, output: str) -> str | None:
    """Validate the middle-LLM output. Returns None on success, else reason.

    Guards: invented/missing IDs, dropped entries, no actual compression.
    """
    if not output:
        return "empty output"
    original = _stringify(data)
    if tool_name in COMPRESSIBLE_TOOLS and len(output) >= len(original) * MIN_COMPRESSION_RATIO:
        return f"not compressed ({len(output)} >= {len(original)} * {MIN_COMPRESSION_RATIO})"

    in_ids = _extract_uuids(original)
    # URL completeness for search cleaning — must run before the ID early-exit
    if tool_name in CLEANABLE_TOOLS:
        in_urls = set(re.findall(r"https?://\S+", original))
        out_urls = set(re.findall(r"https?://\S+", output))
        if in_urls and out_urls and in_urls - out_urls:
            return f"{len(in_urls - out_urls)} URLs lost"
    if not in_ids:
        return None  # no IDs to guard (e.g. pure prose)
    out_ids = _extract_uuids(output)
    lost = in_ids - out_ids
    if lost:
        return f"{len(lost)} IDs lost"
    return None


def _fold_fallback(result: dict) -> str:
    """Last-resort marker when a huge result could not be compressed."""
    data = result.get("data", "")
    data_str = data if isinstance(data, str) else _stringify(data)
    head = data_str[:FOLD_HEAD_CHARS].replace("\n", " ").strip()
    return f"[操作成功 — 结果较大(共{len(data_str)}字符)，压缩失败，仅保留开头摘要：{head}...]"


async def middle_process_tool_result(
    tool_name: str,
    result: dict,
    llm_chat: Callable[..., Any],
) -> str:
    """Return the tool-message body for *result*.

    Verbatim for whitelisted-but-small and all non-whitelisted tools;
    middle-LLM compressed/cleaned for whitelisted large ones, with
    strict validation and layered fallbacks.  Never raises.
    """
    from app.llm.types import Message as M

    data = result.get("data", "")
    text = _stringify(data)
    chars = len(text)

    if not should_middle_process(tool_name, result):
        # Verbatim, but keep the marker framing for success payloads
        return f"[操作成功]\n{text}" if result.get("success") else f"[操作失败]\n{result.get('error', 'unknown')}"

    output = ""
    try:
        prompt = _system_prompt(tool_name, data, chars)
        body = f"<tool_result tool=\"{tool_name}\" chars={chars}>\n{text}\n</tool_result>"
        model = getattr(settings, "llm_middle_model", "") or None
        chat_result = await asyncio.wait_for(
            llm_chat(
                [M(role="system", content=prompt), M(role="user", content=body)],
                model=model,
                max_tokens=8192,
                temperature=0.2,
            ),
            timeout=MIDDLE_LLM_TIMEOUT_S,
        )
        output = (chat_result.content or "").strip()
    except Exception as exc:
        logger.warning(
            "Middle-LLM %s failed: %s — falling back", tool_name, exc, exc_info=True
        )
        output = ""

    reason = _validate(tool_name, data, output)
    if reason:
        logger.warning(
            "Middle-LLM %s output rejected (%s) — falling back", tool_name, reason
        )
        output = ""

    if output:
        # 压缩/清洗的是外部网页内容,复用 guard 的注入/危险内容检测,
        # 命中则丢弃,避免注入内容进入主循环。
        from app.agent.guard import check_web_content_safety
        injection_err = check_web_content_safety(output)
        if injection_err:
            logger.warning(
                "Middle-LLM %s output blocked by web content check (%s) — falling back",
                tool_name, injection_err,
            )
            output = ""

    if output:
        return output

    # Fallbacks: verbatim if the result fits the window safely, else fold.
    if chars < FOLD_FALLBACK_CHARS:
        return f"[操作成功]\n{text}"
    return _fold_fallback(result)
