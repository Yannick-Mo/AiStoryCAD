from __future__ import annotations

import difflib
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.storycad.models import Scene, SceneContent
from app.agent.utils import count_words



def _fuzzy_locate(content: str, snippet: str, threshold: float = 0.6) -> int:
    """Find *snippet* in *content* using fuzzy matching.

    First tries exact match (fast path).  If that fails, uses a sliding
    window with ``difflib.SequenceMatcher`` to find the best-matching
    region whose similarity exceeds *threshold*.

    Returns the start index, or -1 if no match at all.
    """
    # Fast path: exact match
    pos = content.find(snippet)
    if pos != -1:
        return pos

    # Normalise whitespace for comparison
    def _norm(s: str) -> str:
        return " ".join(s.split())

    norm_content = _norm(content)
    norm_snippet = _norm(snippet)

    pos = norm_content.find(norm_snippet)
    if pos != -1:
        # Map back to original content offset by scanning original chars
        # and counting how many we need to reach the norm offset.
        char_count = 0
        norm_idx = 0
        for c in content:
            if norm_idx >= pos:
                break
            char_count += 1
            if c not in (" ", "\n", "\r", "\t"):
                norm_idx += 1
            elif norm_idx > 0 and not content[char_count - 2:char_count].isspace():
                norm_idx += 1
        return char_count

    # Sliding-window fuzzy match
    sn_len = len(snippet)
    ct_len = len(content)
    if sn_len > ct_len:
        return -1

    best_ratio = 0.0
    best_pos = -1
    step = max(1, sn_len // 4)  # slide by quarter-snippet increments
    for start in range(0, ct_len - sn_len + 1, step):
        window = content[start:start + sn_len + 20]  # slight over-read for safety
        ratio = difflib.SequenceMatcher(None, snippet, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = start

    if best_ratio >= threshold:
        return best_pos
    return -1


class WriteSceneContentTool(BaseTool):
    meta = ToolMeta(
        name="write_scene_content",
        description="写入场景正文内容，更新场景字数。scene_id 来自 list_scenes 或 read_full_project",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景ID，来自 list_scenes 或 read_full_project"},
                "content": {"type": "string", "description": "场景正文内容"},
            },
            "required": ["scene_id", "content"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            sc_id_raw = self._require_param(kwargs, "scene_id")
            if sc_id_raw is None:
                return self._missing_param("scene_id")
            content = self._require_param(kwargs, "content")
            if content is None:
                return self._missing_param("content")

            sc_id = uuid.UUID(sc_id_raw)
            result = await db.execute(select(Scene).where(Scene.id == sc_id))
            scene_obj = result.scalar_one_or_none()
            if not scene_obj:
                return self._not_found("Scene")
            await verify_project_owner(db, scene_obj.project_id, kwargs.get("user_id"))
            result = await db.execute(select(SceneContent).where(SceneContent.scene_id == sc_id))
            sc = result.scalar_one_or_none()
            if sc:
                sc.content = content
            else:
                db.add(SceneContent(scene_id=sc_id, project_id=scene_obj.project_id, content=content))
            wc = count_words(content)
            scene_obj.word_count = wc
            await db.commit()
            return ToolResult(success=True, data={"scene_id": str(sc_id), "word_count": wc, "content_preview": content[:200]})
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ContinueSceneTool(BaseTool):
    meta = ToolMeta(
        name="continue_scene",
        description="基于前文风格分析，续写场景内容（追加到已有内容后）。scene_id 来自 list_scenes",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景ID，来自 list_scenes 或 read_full_project"},
                "additional_content": {"type": "string", "description": "续写的内容"},
            },
            "required": ["scene_id", "additional_content"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            sc_id_raw = self._require_param(kwargs, "scene_id")
            if sc_id_raw is None:
                return self._missing_param("scene_id")
            additional = self._require_param(kwargs, "additional_content")
            if additional is None:
                return self._missing_param("additional_content")

            sc_id = uuid.UUID(sc_id_raw)
            result = await db.execute(select(Scene).where(Scene.id == sc_id))
            scene_obj = result.scalar_one_or_none()
            if not scene_obj:
                return self._not_found("Scene")
            await verify_project_owner(db, scene_obj.project_id, kwargs.get("user_id"))
            result = await db.execute(select(SceneContent).where(SceneContent.scene_id == sc_id))
            sc = result.scalar_one_or_none()
            existing = sc.content if sc else ""
            new_content = existing + ("\n\n" if existing else "") + additional
            writer = WriteSceneContentTool(llm_client=self.llm_client)
            return await writer.run(db, user_id=kwargs.get("user_id"), scene_id=str(sc_id), content=new_content)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class RewriteSceneTool(BaseTool):
    meta = ToolMeta(
        name="rewrite_scene",
        description="以指定风格或要求重写场景内容。scene_id 来自 list_scenes",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景ID，来自 list_scenes 或 read_full_project"},
                "content": {"type": "string", "description": "重写后的内容"},
                "style": {"type": "string", "description": "风格说明（更悬疑/更简洁/更有张力等）"},
            },
            "required": ["scene_id", "content"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            sc_id_raw = self._require_param(kwargs, "scene_id")
            if sc_id_raw is None:
                return self._missing_param("scene_id")
            content = self._require_param(kwargs, "content")
            if content is None:
                return self._missing_param("content")

            sc_id = uuid.UUID(sc_id_raw)
            result = await db.execute(select(Scene).where(Scene.id == sc_id))
            scene_obj = result.scalar_one_or_none()
            if not scene_obj:
                return self._not_found("Scene")
            await verify_project_owner(db, scene_obj.project_id, kwargs.get("user_id"))
            writer = WriteSceneContentTool(llm_client=self.llm_client)
            return await writer.run(db, user_id=kwargs.get("user_id"), scene_id=str(sc_id), content=content)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class ExpandSelectionTool(BaseTool):
    meta = ToolMeta(
        name="expand_selection",
        description="扩写指定段落，保持风格一致。scene_id 来自 list_scenes，original_text 需与场景正文精确匹配",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景ID，来自 list_scenes 或 read_full_project"},
                "original_text": {"type": "string", "description": "原始文本（用于定位，需先调用 read_scene 获取精确文本）"},
                "expanded_text": {"type": "string", "description": "扩写后的完整段落"},
            },
            "required": ["scene_id", "original_text", "expanded_text"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            sc_id_raw = self._require_param(kwargs, "scene_id")
            if sc_id_raw is None:
                return self._missing_param("scene_id")
            original = self._require_param(kwargs, "original_text")
            if original is None:
                return self._missing_param("original_text")
            expanded = self._require_param(kwargs, "expanded_text")
            if expanded is None:
                return self._missing_param("expanded_text")

            sc_id = uuid.UUID(sc_id_raw)
            result = await db.execute(select(Scene).where(Scene.id == sc_id))
            scene_obj = result.scalar_one_or_none()
            if not scene_obj:
                return self._not_found("Scene")
            await verify_project_owner(db, scene_obj.project_id, kwargs.get("user_id"))
            result = await db.execute(select(SceneContent).where(SceneContent.scene_id == sc_id))
            sc = result.scalar_one_or_none()
            if not sc:
                return self._not_found("SceneContent")
            pos = _fuzzy_locate(sc.content, original)
            if pos == -1:
                return ToolResult(
                    success=False,
                    error="无法在场景正文中找到指定的原始文本，可能存在标点、空格或换行差异",
                    correction_hint="请先使用场景读取工具获取场景完整正文，复制需要替换的精确文本后重新调用本工具",
                )
            new_content = sc.content[:pos] + expanded + sc.content[pos + len(original):]
            writer = WriteSceneContentTool(llm_client=self.llm_client)
            return await writer.run(db, user_id=kwargs.get("user_id"), scene_id=str(sc_id), content=new_content)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


class CompressSelectionTool(BaseTool):
    meta = ToolMeta(
        name="compress_selection",
        description="压缩指定段落，保持关键信息。scene_id 来自 list_scenes",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景ID，来自 list_scenes 或 read_full_project"},
                "original_text": {"type": "string", "description": "原始文本（需先调用 read_scene 获取精确文本）"},
                "compressed_text": {"type": "string", "description": "压缩后的文本"},
            },
            "required": ["scene_id", "original_text", "compressed_text"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            sc_id_raw = self._require_param(kwargs, "scene_id")
            if sc_id_raw is None:
                return self._missing_param("scene_id")
            original = self._require_param(kwargs, "original_text")
            if original is None:
                return self._missing_param("original_text")
            compressed = self._require_param(kwargs, "compressed_text")
            if compressed is None:
                return self._missing_param("compressed_text")

            sc_id = uuid.UUID(sc_id_raw)
            result = await db.execute(select(Scene).where(Scene.id == sc_id))
            scene_obj = result.scalar_one_or_none()
            if not scene_obj:
                return self._not_found("Scene")
            await verify_project_owner(db, scene_obj.project_id, kwargs.get("user_id"))
            result = await db.execute(select(SceneContent).where(SceneContent.scene_id == sc_id))
            sc = result.scalar_one_or_none()
            if not sc:
                return self._not_found("SceneContent")
            pos = _fuzzy_locate(sc.content, original)
            if pos == -1:
                return ToolResult(
                    success=False,
                    error="无法在场景正文中找到指定的原始文本，可能存在标点、空格或换行差异",
                    correction_hint="请先使用场景读取工具获取场景完整正文，复制需要替换的精确文本后重新调用本工具",
                )
            new_content = sc.content[:pos] + compressed + sc.content[pos + len(original):]
            writer = WriteSceneContentTool(llm_client=self.llm_client)
            return await writer.run(db, user_id=kwargs.get("user_id"), scene_id=str(sc_id), content=new_content)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))
