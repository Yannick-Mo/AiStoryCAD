from __future__ import annotations

import json
import uuid
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.llm.client import get_shared_client
from app.llm.types import Message
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.agent.context import ContextBuilder


def _safe_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safely traverse a nested dict/list structure."""
    current = obj
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, (list, tuple)) and isinstance(key, int):
            try:
                current = current[key]
            except (IndexError, TypeError):
                return default
        else:
            return default
    return current if current is not None else default


class AnalyzeChapterTool(BaseTool):
    meta = ToolMeta(
        name="analyze_chapter",
        description="分析指定章节的结构、节奏、角色、语言，返回四维评分和改进建议",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 read_chapters（范围读取）或 read_chapter"},
            },
            "required": ["chapter_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            ch_raw = self._require_param(kwargs, "chapter_id")
            if ch_raw is None:
                return self._missing_param("chapter_id")
            project_id = uuid.UUID(pid_raw)
            await verify_project_owner(db, project_id, kwargs.get("user_id"))
            chapter_id = uuid.UUID(ch_raw)

            builder = ContextBuilder(db)
            focus = await builder.build_chapter_focus(project_id, chapter_id)
            if focus is None:
                return ToolResult(success=False, error="Chapter not found")

            chapter = focus["chapter"]
            act = focus.get("act") or {}
            scenes = focus.get("scenes", [])

            scenes_text = []
            for sc in scenes:
                body = sc.get("content") or ""
                scenes_text.append(
                    f"【{sc.get('title', '')}】(POV:{sc.get('pov_character', '')} "
                    f"地点:{sc.get('setting', '')} 时间:{sc.get('scene_time', '')})\n"
                    f"梗概:{sc.get('summary', '') or '无'}\n"
                    f"正文:\n{body}"
                    + ("\n[该场景正文过长，已截断]" if sc.get("content_cut") else "")
                )

            neighbour = []
            prev_ch = focus.get("prev_chapter")
            next_ch = focus.get("next_chapter")
            if prev_ch:
                neighbour.append(f"上一章《{prev_ch.get('title', '')}》目标:{prev_ch.get('goal', '') or '无'}")
            if next_ch:
                neighbour.append(f"下一章《{next_ch.get('title', '')}》目标:{next_ch.get('goal', '') or '无'}")

            context_text = json.dumps({
                "chapter": {"title": chapter.get("title"), "goal": chapter.get("goal"),
                            "status": chapter.get("status")},
                "act": act.get("name") or "",
                "scenes_count": focus.get("scene_count", 0),
                "chapter_body_chars": focus.get("body_chars", 0),
            }, ensure_ascii=False)

            client = self.llm_client or get_shared_client().fork()
            msgs = [
                Message(
                    role="system",
                    content=(
                        "你是专业的文学分析专家。请从以下四个维度分析指定章节，"
                        "每个维度给出0-10的评分和具体分析：\n"
                        "1. 结构（起承转合是否完整）\n"
                        "2. 节奏（张弛是否得当）\n"
                        "3. 角色（人物塑造是否一致）\n"
                        "4. 语言（文笔和表达质量）\n\n"
                        "输出JSON格式："
                        "{'scores': {'structure': int, 'pacing': int, 'character': int, 'language': int}, "
                        "'analysis': str, 'suggestions': [str]}"
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        f"项目上下文：{context_text}\n\n"
                        f"章节：{chapter.get('title')}\n"
                        f"目标：{chapter.get('goal') or '无'}\n\n"
                        + ("邻接章节（仅作上下文参考，不要分析它们）：\n"
                           + "\n".join(neighbour) + "\n\n" if neighbour else "")
                        + f"场景：\n" + "\n\n".join(scenes_text)
                    ),
                ),
            ]
            result = await client.chat(messages=msgs)
            try:
                parsed = json.loads(result.content or "{}")
            except json.JSONDecodeError:
                parsed = {"scores": {}, "analysis": result.content, "suggestions": [],
                          "_parse_note": "LLM 返回了非 JSON 格式的回复，以上为原始文本，未成功解析为结构化数据"}

            if focus.get("content_truncated"):
                parsed["_note"] = "本章正文字数超过分析上限，尾部场景正文被截断"

            return ToolResult(success=True, data=parsed)
        except Exception as e:
            await db.rollback()
            return self._err(e)


class AnalyzeCharacterArcTool(BaseTool):
    meta = ToolMeta(
        name="analyze_character_arc",
        description="分析角色的弧线发展和一致性",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "character_id": {"type": "string", "description": "角色ID，来自 list_characters 返回结果"},
            },
            "required": ["character_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            ch_raw = self._require_param(kwargs, "character_id")
            if ch_raw is None:
                return self._missing_param("character_id")
            project_id = uuid.UUID(pid_raw)
            await verify_project_owner(db, project_id, kwargs.get("user_id"))
            char_id = uuid.UUID(ch_raw)

            builder = ContextBuilder(db)
            focus = await builder.build_character_focus(project_id, char_id)
            if focus is None:
                return ToolResult(success=False, error="Character not found")

            client = self.llm_client or get_shared_client().fork()
            msgs = [
                Message(
                    role="system",
                    content=(
                        "你是角色分析专家。分析角色的弧线发展、一致性和潜在问题。"
                        "输出JSON："
                        "{'arc_type': str, 'consistency_score': int, 'analysis': str, "
                        "'issues': [str], 'suggestions': [str]}"
                    ),
                ),
                Message(role="user", content=json.dumps(focus, ensure_ascii=False)),
            ]
            result = await client.chat(messages=msgs)
            try:
                parsed = json.loads(result.content or "{}")
            except json.JSONDecodeError:
                parsed = {"analysis": result.content, "issues": [],
                          "_parse_note": "LLM 返回了非 JSON 格式的回复，以上为原始文本，未成功解析为结构化数据"}

            return ToolResult(success=True, data=parsed)
        except Exception as e:
            await db.rollback()
            return self._err(e)


class SuggestNextTool(BaseTool):
    meta = ToolMeta(
        name="suggest_next",
        description="基于当前项目进展，推荐下一步该写什么",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            builder = ContextBuilder(db)
            progress = await builder.build_writing_progress(pid)

            summary = {k: progress[k] for k in (
                "total_acts", "total_chapters", "total_scenes",
                "written_scenes", "progress_pct",
            )}

            client = self.llm_client or get_shared_client().fork()
            msgs = [
                Message(
                    role="system",
                    content=(
                        "你是写作进度顾问。根据项目当前状态，推荐用户接下来应该写什么。"
                        "输出JSON："
                        "{'focus': str, 'reason': str, 'suggested_scene': str, 'tips': [str]}"
                    ),
                ),
                Message(
                    role="user",
                    content=json.dumps(
                        {
                            "summary": summary,
                            "recent_written": progress.get("recent_written", []),
                            "unwritten_scenes": progress.get("unwritten_candidates", [])[:20],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
            result = await client.chat(messages=msgs)
            try:
                parsed = json.loads(result.content or "{}")
            except json.JSONDecodeError:
                parsed = {"focus": result.content,
                          "_parse_note": "LLM 返回了非 JSON 格式的回复，以上为原始文本，未成功解析为结构化数据"}

            return ToolResult(success=True, data={**summary, **parsed})
        except Exception as e:
            await db.rollback()
            return self._err(e)


class ProjectHealthTool(BaseTool):
    meta = ToolMeta(
        name="project_health",
        description="全项目健康检查：未完场景、空章节、孤立角色、未回收伏笔",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            pid_raw = self._require_param(kwargs, "project_id")
            if pid_raw is None:
                return self._missing_param("project_id")
            pid = uuid.UUID(pid_raw)
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            builder = ContextBuilder(db)
            snapshot = await builder.build_health_snapshot(pid)
            return ToolResult(success=True, data=snapshot)
        except Exception as e:
            await db.rollback()
            return self._err(e)
