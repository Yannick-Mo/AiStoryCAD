from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agents.writer_agent import WritingAgent
from app.agent.context import ContextBuilder
from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolMeta, ToolResult, verify_project_owner
from app.agent.utils import count_words
from app.storycad.models import Chapter
from app.storycad.repository import with_chapter_content

logger = logging.getLogger(__name__)


def _action_label(action: str) -> str:
    return {"continue": "续写", "rewrite": "重写"}.get(action, "创作")


class CallWriterAgentTool(BaseTool):
    """调用专业写作智能体创作**章**正文。

    工作流程：

    1. 通过 ``ContextBuilder.build_for_writing()`` 获取专注的写作上下文（章级）；
    2. 调用 WritingAgent 生成正文（纯写作 prompt，无工具干扰）；
    3. 直接保存到 DB，只返回摘要给主 LLM——避免上万字正文在主 LLM 上下文里膨胀。

    保存语义按 action 区分：``continue`` **追加**（不重发已有正文），
    ``write`` / ``rewrite`` **整体替换**。这三者在数据层是同一个操作，
    差别只在写手侧的提示词——所以它不是一个"独立的工具"。
    """

    meta = ToolMeta(
        name="call_writer_agent",
        description=(
            "调用专业写作智能体创作一章的正文（创作／续写／重写），完成后自动保存，"
            "只把摘要返回给你。本工具会自动加载：本章要写什么、上一章蓝图与结尾原文、"
            "这一幕要干什么、主线骨架与终局、人物想要什么、还没收的伏笔、全局设定。"
            "这些不必先读，也不要写进 user_requirement。"
        ),
        concurrency=ConcurrencyMode.EXCLUSIVE,
        # 写手一次生成一整章：读超时 600 秒（见 WritingAgent._WRITE_READ_TIMEOUT_S），
        # 工具这一层必须比它宽，否则正文已经在写了却被工具超时掐掉。
        timeout=660,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {
                    "type": "string",
                    "description": "章节ID，来自 read_chapters 或 read_acts",
                },
                "action": {
                    "type": "string",
                    "enum": ["write", "continue", "rewrite"],
                    "description": "write=新写（整体替换）/ continue=续写（在章末追加）/ rewrite=重写（整体替换，保留情节骨架、改善文笔）",
                },
                "user_requirement": {
                    "type": "string",
                    "description": (
                        "只装用户对这次写作的要求：风格（如「写成无声的」「更克制」）、"
                        "篇幅、想强调的点或禁忌；照用户的原话说，不要改写成任务简报。"
                        "用户这一轮没提要求就省略本参数，不要为了填而编。"
                        "不要复述本章蓝图、人物设定、世界观、上一章发生了什么，"
                        "也不要重述本章要发生的事——这些工具会自动加载。"
                    ),
                },
            },
            "required": ["chapter_id", "action"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            action = kwargs.get("action")
            if action not in ("write", "continue", "rewrite"):
                return ToolResult(
                    success=False,
                    error="参数缺失: 工具需要 action 参数但未提供或取值非法（可选值：write/continue/rewrite）。"
                          "不要省略该参数，否则将默认整体替换本章正文。",
                    correction_hint="下一步：明确指定 action=write（新写）/continue（续写追加）/rewrite（重写）后再调用本工具",
                )
            user_requirement = kwargs.get("user_requirement", "")

            ch_id = uuid.UUID(ch_raw)
            chapter = (await db.execute(
                with_chapter_content(select(Chapter).where(Chapter.id == ch_id))
            )).scalar_one_or_none()
            if not chapter:
                return self._not_found("Chapter")
            await verify_project_owner(db, chapter.project_id, kwargs.get("user_id"))

            # 1. 专注的写作上下文（章级）
            builder = ContextBuilder(db)
            ctx = await builder.build_for_writing(ch_id, action)

            # 2. 注入用户要求（只装用户意愿；蓝图等由 build_for_writing 自动加载）
            if user_requirement:
                ctx["user_requirement"] = user_requirement

            # 写作链路**不加载任何技能**：写手是纯文本生成器——题材取向由项目
            # 全局设定与章蓝图提供。主 Agent 侧仍保留技能。

            ctx["action"] = action

            # 3. 调用写作智能体
            agent = WritingAgent()
            user_prompt = (
                f"请{_action_label(action)}《{ctx.get('chapter_title', '')}》的正文。"
            )
            writing = await agent.run(self.llm_client, ctx, user_prompt)
            text = writing.text
            if not text:
                return ToolResult(success=False, error="写作智能体未能生成正文")

            # 4. 保存：续写追加，其余整体替换
            existing = chapter.content or ""
            if action == "continue":
                chapter.content = existing + text
            else:
                chapter.content = text
            await db.commit()

            wc = count_words(chapter.content)
            preview = text[:200].replace("\n", " ")
            truncated = getattr(writing, "truncated", False)
            title = ctx.get("chapter_title", "")

            if truncated:
                return ToolResult(
                    success=True,
                    data={
                        "chapter_id": ch_raw,
                        "word_count": wc,
                        "action": action,
                        "preview": preview,
                        "truncated": True,
                        "summary": f"已{_action_label(action)}章《{title}》，本章现共 {wc} 字，"
                                   "但⚠️模型输出在长度上限处被截断，正文尾部可能缺失。",
                    },
                    correction_hint=(
                        "正文可能不完整：用本工具再要一次（action=continue）把正文补全——"
                        "补全之前不要基于本章结尾继续往下写下一章。"
                    ),
                )

            return ToolResult(
                success=True,
                data={
                    "chapter_id": ch_raw,
                    "word_count": wc,
                    "action": action,
                    "preview": preview,
                    "summary": f"已{_action_label(action)}章《{title}》，本章现共 {wc} 字。",
                    "reminder": (
                        "本章正文就是下一章的前情，不用再写摘要——写手会直接读这一章的结尾原文。"
                        "只有两件事需要落库：①本章长出了计划之外的活线，用 create_setup 记下来"
                        "（挖到的信息、埋下的物件、关系的变化）；②本章新立的硬数字"
                        "（年数、价钱、份额、期限），后面每一章都照它写，别让它随正文一起消失。"
                    ),
                },
                correction_hint="如需调整内容，可以重新调用本工具，并在 user_requirement 里说明修改方向",
            )
        except Exception as e:
            await db.rollback()
            logger.error("CallWriterAgentTool failed: %s", e, exc_info=True)
            return self._err(e)
