"""内容写入工具——规格第 9 节「内容」三个里的两个。

没有独立的"重写"工具：``write_chapter`` 本来就是整体替换，重写和它完全同一个操作，
差别只在写手侧的提示词（保留情节骨架、改善文笔）。所以 ``rewrite_scene`` 并进这里。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.base import BaseTool, ConcurrencyMode, ToolMeta, ToolResult, verify_project_owner
from app.agent.utils import count_words
from app.storycad.models import Chapter
from app.storycad.repository import with_chapter_content


class WriteChapterTool(BaseTool):
    meta = ToolMeta(
        name="write_chapter",
        description="写入或**整体替换**一章正文（**重写也用它**）。⚠️ 会覆盖这一章已有的正文："
                    "先 read_chapter_content 读完再改；只想往后接请用 continue_chapter。"
                    "写完不用再补任何摘要——下一章直接读这一章的结尾原文",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        is_destructive=True,
        timeout=60,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 read_chapters 或 read_acts"},
                "content": {"type": "string", "description": "这一章的完整正文（整体替换，会覆盖旧文）"},
            },
            "required": ["chapter_id", "content"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            content = kwargs.get("content")
            if content is None or (isinstance(content, str) and not content.strip()):
                return self._missing_param("content")
            chapter = (await db.execute(
                with_chapter_content(select(Chapter).where(Chapter.id == uuid.UUID(ch_raw)))
            )).scalar_one_or_none()
            if not chapter:
                return self._not_found("Chapter")
            await verify_project_owner(db, chapter.project_id, kwargs.get("user_id"))

            replaced_chars = len(chapter.content or "")
            chapter.content = content
            await db.commit()
            return ToolResult(success=True, data={
                "chapter_id": str(chapter.id),
                "title": chapter.title or "",
                "word_count": count_words(content),
                "body_chars": len(content),
                "replaced_body_chars": replaced_chars,
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ContinueChapterTool(BaseTool):
    meta = ToolMeta(
        name="continue_chapter",
        description="在章末**追加**（不重发已有正文，省 token）。用于一章一次没写完、或接着上次停笔的地方往下写。"
                    "追加处不加分隔符，内容要能自然接上上一句",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        timeout=60,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 read_chapters 或 read_acts"},
                "additional_content": {"type": "string", "description": "要接在章末的后续正文"},
            },
            "required": ["chapter_id", "additional_content"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            addition = kwargs.get("additional_content")
            if addition is None or (isinstance(addition, str) and not addition.strip()):
                return self._missing_param("additional_content")
            chapter = (await db.execute(
                with_chapter_content(select(Chapter).where(Chapter.id == uuid.UUID(ch_raw)))
            )).scalar_one_or_none()
            if not chapter:
                return self._not_found("Chapter")
            await verify_project_owner(db, chapter.project_id, kwargs.get("user_id"))

            existing = chapter.content or ""
            chapter.content = existing + addition
            await db.commit()
            return ToolResult(success=True, data={
                "chapter_id": str(chapter.id),
                "title": chapter.title or "",
                "word_count": count_words(chapter.content),
                "body_chars": len(chapter.content),
                "appended_chars": len(addition),
            })
        except Exception as e:
            await db.rollback()
            return self._err(e)
