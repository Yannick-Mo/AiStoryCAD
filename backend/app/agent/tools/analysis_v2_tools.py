from __future__ import annotations

import json
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.llm.client import get_shared_client
from app.llm.types import Message
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.agent.context import ContextBuilder
from app.storycad.models import Chapter, Scene, SceneContent, Character, CharacterRelation, Act
from app.utils import row_to_dict


class AnalyzeChapterTool(BaseTool):
    meta = ToolMeta(
        name="analyze_chapter",
        description="分析指定章节的结构、节奏、角色、语言，返回四维评分和改进建议",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {"type": "string", "description": "章节ID，来自 list_chapters 或 read_full_project"},
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

            # 安全：按项目过滤章节查询，防止跨项目按 UUID 读取（IDOR）
            ch_result = await db.execute(
                select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
            )
            chapter = ch_result.scalar_one_or_none()
            if not chapter:
                return ToolResult(success=False, error="Chapter not found")

            scenes_result = await db.execute(
                select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.sort_order)
            )
            scenes = scenes_result.scalars().all()
            scenes_text = []
            for sc in scenes:
                cr = await db.execute(select(SceneContent).where(SceneContent.scene_id == sc.id))
                content = cr.scalar_one_or_none()
                scenes_text.append(
                    f"【{sc.title}】(POV:{sc.pov_character} 地点:{sc.setting})\n"
                    f"{sc.summary or '无梗概'}\n"
                    f"正文预览:{(content.content or '')[:500] if content else '空'}"
                )

            builder = ContextBuilder(db)
            full_ctx = await builder.build_full(project_id)

            act_data = None
            if chapter.act_id:
                act_result = await db.execute(select(Act).where(Act.id == chapter.act_id))
                act_obj = act_result.scalar_one_or_none()
                if act_obj:
                    act_data = row_to_dict(act_obj)

            context_text = json.dumps({
                "chapter": {"title": chapter.title, "goal": chapter.goal, "status": chapter.status},
                "scenes_count": len(scenes),
                "act": act_data,
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
                        f"章节：{chapter.title}\n"
                        f"目标：{chapter.goal}\n\n"
                        f"场景：\n" + "\n\n".join(scenes_text)
                    ),
                ),
            ]
            result = await client.chat(messages=msgs)
            try:
                parsed = json.loads(result.content or "{}")
            except json.JSONDecodeError:
                parsed = {"scores": {}, "analysis": result.content, "suggestions": [],
                          "_parse_note": "LLM 返回了非 JSON 格式的回复，以上为原始文本，未成功解析为结构化数据"}

            return ToolResult(success=True, data=parsed)
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))


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

            # 安全：按项目过滤角色查询，防止跨项目按 UUID 读取（IDOR）
            result = await db.execute(
                select(Character).where(Character.id == char_id, Character.project_id == project_id)
            )
            char = result.scalar_one_or_none()
            if not char:
                return ToolResult(success=False, error="Character not found")

            scenes_result = await db.execute(
                select(Scene).where(Scene.project_id == project_id).order_by(Scene.sort_order)
            )
            all_scenes = scenes_result.scalars().all()
            char_scenes = []
            for sc in all_scenes:
                if sc.pov_character == char.name:
                    cr = await db.execute(select(SceneContent).where(SceneContent.scene_id == sc.id))
                    content = cr.scalar_one_or_none()
                    char_scenes.append(
                        f"场景「{sc.title}」(POV:{sc.pov_character}) - "
                        f"{(content.content or '')[:300] if content else '空'}"
                    )

            rels_result = await db.execute(
                select(CharacterRelation).where(
                    CharacterRelation.project_id == project_id,
                    (CharacterRelation.character_id == char_id) | (CharacterRelation.target_id == char_id)
                )
            )
            rels = rels_result.scalars().all()

            context = {
                "character": row_to_dict(char),
                "appearances": char_scenes,
                "relations": [row_to_dict(r) for r in rels],
            }

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
                Message(role="user", content=json.dumps(context, ensure_ascii=False)),
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
            return ToolResult(success=False, error=str(e))


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
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            builder = ContextBuilder(db)
            full_ctx = await builder.build_full(pid)

            acts = full_ctx.get("acts", []) if isinstance(full_ctx, dict) else []
            total_chapters = sum(len(_safe_get(a, "chapters", default=[])) for a in acts)
            total_scenes = sum(
                sum(len(_safe_get(ch, "scenes", default=[])) for ch in _safe_get(a, "chapters", default=[]))
                for a in acts
            )
            written_scenes = sum(
                sum(
                    1 for ch in _safe_get(a, "chapters", default=[])
                    for s in _safe_get(ch, "scenes", default=[]) if s.get("content_preview")
                )
                for a in acts
            )

            summary = {
                "total_acts": len(acts),
                "total_chapters": total_chapters,
                "total_scenes": total_scenes,
                "written_scenes": written_scenes,
                "progress_pct": round(written_scenes / total_scenes * 100) if total_scenes else 0,
            }

            unwritten = []
            for a in acts:
                for ch in _safe_get(a, "chapters", default=[]):
                    for s in _safe_get(ch, "scenes", default=[]):
                        if not s.get("content_preview"):
                            unwritten.append(
                                f"幕'{a.get('name', '')}'→章'{ch.get('title', '')}'→场景'{s.get('title', '')}'"
                            )

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
                        {"summary": summary, "unwritten_scenes": unwritten[:20]},
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
            return ToolResult(success=False, error=str(e))


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
            pid = uuid.UUID(kwargs["project_id"])
            await verify_project_owner(db, pid, kwargs.get("user_id"))
            builder = ContextBuilder(db)
            full_ctx = await builder.build_full(pid)

            acts = full_ctx.get("acts", []) if isinstance(full_ctx, dict) else []
            chars = full_ctx.get("characters", []) if isinstance(full_ctx, dict) else []
            edges = full_ctx.get("edges", []) if isinstance(full_ctx, dict) else []

            empty_chapters = []
            unwritten_scenes = []
            for a in acts:
                for ch in _safe_get(a, "chapters", default=[]):
                    if not _safe_get(ch, "scenes", default=[]):
                        empty_chapters.append(
                            f"幕'{a.get('name', '')}'→章'{ch.get('title', '')}'（无场景）"
                        )
                    for s in _safe_get(ch, "scenes", default=[]):
                        if not s.get("content_preview"):
                            unwritten_scenes.append(
                                f"幕'{a.get('name', '')}'→章'{ch.get('title', '')}'→场景'{s.get('title', '')}'"
                            )

            chars_with_relations = set()
            for r in full_ctx.get("relations", []) if isinstance(full_ctx, dict) else []:
                chars_with_relations.add(r.get("character_id"))
                chars_with_relations.add(r.get("target_id"))
            isolated_chars = [
                c.get("name", "") for c in chars
                if str(c.get("id", "")) not in chars_with_relations
            ]

            return ToolResult(success=True, data={
                "total_acts": len(acts),
                "total_chapters": sum(len(_safe_get(a, "chapters", default=[])) for a in acts),
                "total_scenes": sum(
                    sum(len(_safe_get(ch, "scenes", default=[])) for ch in _safe_get(a, "chapters", default=[]))
                    for a in acts
                ),
                "unwritten_scenes_count": len(unwritten_scenes),
                "unwritten_scenes": unwritten_scenes[:20],
                "empty_chapters_count": len(empty_chapters),
                "empty_chapters": empty_chapters[:10],
                "total_characters": len(chars),
                "isolated_characters_count": len(isolated_chars),
                "isolated_characters": isolated_chars[:10],
                "total_edges": len(edges),
            })
        except Exception as e:
            await db.rollback()
            return ToolResult(success=False, error=str(e))
