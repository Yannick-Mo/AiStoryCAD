import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.project.models import Project
from app.storycad.models import Character, Chapter, Scene, SceneContent, ChapterEdge
from app.agent.consistency.models import ConsistencyIssue, ConsistencyReport
from app.llm.client import LLMClient, get_shared_client
from app.llm.types import Message
from app.utils import row_to_dict
import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "你是一个故事一致性分析专家。请以纯JSON格式输出分析结果，不要包含markdown代码块标记或其他非JSON内容。"

_PER_CHECK_SYSTEM_PROMPTS: dict[str, str] = {
    "character": (
        "你是一个故事角色一致性分析专家。你的任务是从以下维度找出角色设定与场景内容之间的不一致问题：\n"
        "1. 角色性格前后矛盾\n"
        "2. 角色行为不符合其设定（背景、能力、知识等）\n"
        "3. 角色能力/知识在不同场景中不一致\n"
        "4. 角色关系与设定描述不符\n\n"
        "请以JSON格式输出分析结果。每个issue的severity字段必须取 error / warning / info 之一（不要使用竖线|分隔枚举值）。\n"
        "输出结构示例：\n"
        '{"issues":[{"check_type":"character","severity":"warning",'
        '"entity_type":"character","entity_id":"...","description":"...",'
        '"suggestion":"...","chapter_id":"...","scene_id":"..."}]}'
    ),
    "timeline": (
        "你是一个故事时间线一致性分析专家。你的任务是从以下维度找出时间线逻辑问题：\n"
        "1. 时间跳跃不合理\n"
        "2. 场景顺序错误（因果关系或时间顺序颠倒）\n"
        "3. 时间线矛盾（同一事件在不同场景中时间不一致）\n"
        "4. 章节连接关系与时序标签矛盾\n\n"
        "请以JSON格式输出分析结果。每个issue的severity字段必须取 error / warning / info 之一（不要使用竖线|分隔枚举值）。\n"
        "输出结构示例：\n"
        '{"issues":[{"check_type":"timeline","severity":"warning",'
        '"entity_type":"timeline","entity_id":"...","description":"...",'
        '"suggestion":"...","chapter_id":"...","scene_id":"..."}]}'
    ),
    "world": (
        "你是一个故事世界观一致性分析专家。你的任务是从以下维度找出故事元素与世界观设定之间的矛盾：\n"
        "1. 场景中出现的物品/技术/规则与世界观设定不符\n"
        "2. 角色行为违背世界观中的基本规则\n"
        "3. 不同场景对同一世界观规则的解释不一致\n\n"
        "请以JSON格式输出分析结果。每个issue的severity字段必须取 error / warning / info 之一（不要使用竖线|分隔枚举值）。\n"
        "输出结构示例：\n"
        '{"issues":[{"check_type":"world_rule","severity":"warning",'
        '"entity_type":"world","entity_id":"...","description":"...",'
        '"suggestion":"...","chapter_id":"...","scene_id":"..."}]}'
    ),
}


def _parse_llm_issues(content: str) -> list[ConsistencyIssue]:
    json_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', content)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            items = data if isinstance(data, list) else data.get("issues", [])
            if isinstance(items, list):
                return [ConsistencyIssue(**item) for item in items if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass
    try:
        data = json.loads(content)
        items = data if isinstance(data, list) else data.get("issues", [])
        if isinstance(items, list):
            return [ConsistencyIssue(**item) for item in items if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass
    start = content.find('[')
    end = content.rfind(']')
    if start != -1 and end > start:
        try:
            data = json.loads(content[start:end + 1])
            items = data if isinstance(data, list) else data.get("issues", [])
            if isinstance(items, list):
                return [ConsistencyIssue(**item) for item in items if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass
    return []


class ConsistencyChecker:
    def __init__(self, db: AsyncSession, llm_client: LLMClient | None = None):
        self.db = db
        self._llm = llm_client or get_shared_client().fork()
        self._loaded_characters: list[dict] | None = None
        self._loaded_chapters: list[dict] | None = None
        self._loaded_scenes: list[dict] | None = None
        self._loaded_contents: dict[str, str] | None = None

    async def check_all(self, project_id: str) -> ConsistencyReport:
        pid = uuid.UUID(project_id)
        characters = await self._load_characters(pid)
        chapters = await self._load_chapters(pid)
        scenes = await self._load_scenes(pid)
        contents = await self._load_contents(scenes)

        proj = await self.db.execute(select(Project).where(Project.id == pid))
        proj_obj = proj.scalar_one_or_none()
        global_settings = proj_obj.global_settings if proj_obj else ""

        result_edges = await self.db.execute(
            select(ChapterEdge).where(ChapterEdge.project_id == pid)
        )
        edges = [row_to_dict(e) for e in result_edges.scalars().all()]

        char_task = self._check_char_llm(characters, chapters, scenes, contents)
        timeline_task = self._check_timeline_llm(chapters, scenes, edges)
        world_task = self._check_world_llm(global_settings, characters, scenes)
        llm_results = await asyncio.gather(char_task, timeline_task, world_task, return_exceptions=True)

        self._loaded_characters = characters
        self._loaded_chapters = chapters
        self._loaded_scenes = scenes
        self._loaded_contents = contents

        all_issues = []
        fallbacks = [
            (self._rule_characters, (characters, scenes, chapters)),
            (self._rule_timeline, (chapters, scenes)),
            (self._rule_world, (global_settings, characters, scenes)),
        ]
        for i, ((fallback, args), result) in enumerate(zip(fallbacks, llm_results)):
            if isinstance(result, Exception):
                logger.warning("LLM check %d failed, using rule fallback: %s", i, result)
            elif isinstance(result, list) and result:
                all_issues.extend(result)
                continue
            all_issues.extend(fallback(*args))

        errors = sum(1 for i in all_issues if i.severity == "error")
        warnings = sum(1 for i in all_issues if i.severity == "warning")
        infos = sum(1 for i in all_issues if i.severity == "info")

        if errors == 0 and warnings == 0:
            summary = "未发现一致性问题"
        else:
            summary = f"发现 {errors} 个错误, {warnings} 个警告, {infos} 个提示"

        return ConsistencyReport(
            project_id=project_id,
            issues=all_issues,
            summary=summary,
            timestamp=datetime.now(timezone.utc),
        )

    async def _check_char_llm(self, characters, chapters, scenes, contents_map) -> list[ConsistencyIssue] | None:
        if not characters:
            return None
        prompt = self._build_character_prompt(characters, chapters, scenes, contents_map)
        system = _SYSTEM_PROMPT + "\n\n" + _PER_CHECK_SYSTEM_PROMPTS["character"]
        result = await self._llm.chat(
            messages=[
                Message(role="system", content=system),
                Message(role="user", content=prompt),
            ],
        )
        return _parse_llm_issues(result.content) if result.content else None

    async def _check_timeline_llm(self, chapters, scenes, edges) -> list[ConsistencyIssue] | None:
        if not chapters and not scenes:
            return None
        prompt = self._build_timeline_prompt(chapters, scenes, edges or [])
        system = _SYSTEM_PROMPT + "\n\n" + _PER_CHECK_SYSTEM_PROMPTS["timeline"]
        result = await self._llm.chat(
            messages=[
                Message(role="system", content=system),
                Message(role="user", content=prompt),
            ],
        )
        return _parse_llm_issues(result.content) if result.content else None

    async def _check_world_llm(self, global_settings, characters, scenes) -> list[ConsistencyIssue] | None:
        prompt = self._build_world_prompt(global_settings, characters, scenes)
        result = await self._llm.chat(
            messages=[
                Message(role="system", content=_SYSTEM_PROMPT),
                Message(role="user", content=prompt),
            ],
        )
        return _parse_llm_issues(result.content) if result.content else None

    async def check_character(self, project_id: str) -> list[ConsistencyIssue]:
        pid = uuid.UUID(project_id)
        characters = await self._load_characters(pid)
        if not characters:
            return []
        chapters = await self._load_chapters(pid)
        scenes = await self._load_scenes(pid)
        contents_map = await self._load_contents(scenes)
        try:
            issues = await self._check_char_llm(characters, chapters, scenes, contents_map)
            if issues:
                return issues
        except Exception:
            pass
        return self._rule_characters(characters, scenes, chapters)

    async def check_timeline(self, project_id: str) -> list[ConsistencyIssue]:
        pid = uuid.UUID(project_id)
        chapters = await self._load_chapters(pid)
        scenes = await self._load_scenes(pid)
        if not chapters and not scenes:
            return []
        result_edges = await self.db.execute(
            select(ChapterEdge).where(ChapterEdge.project_id == pid)
        )
        edges = [row_to_dict(e) for e in result_edges.scalars().all()]
        try:
            issues = await self._check_timeline_llm(chapters, scenes, edges)
            if issues:
                return issues
        except Exception:
            pass
        return self._rule_timeline(chapters, scenes)

    async def check_world_rules(self, project_id: str) -> list[ConsistencyIssue]:
        pid = uuid.UUID(project_id)
        proj = await self.db.execute(select(Project).where(Project.id == pid))
        proj_obj = proj.scalar_one_or_none()
        global_settings = proj_obj.global_settings if proj_obj else ""
        characters = await self._load_characters(pid)
        scenes = await self._load_scenes(pid)
        try:
            issues = await self._check_world_llm(global_settings, characters, scenes)
            if issues:
                return issues
        except Exception:
            pass
        return self._rule_world(global_settings, characters, scenes)

    # ---- helpers ----

    async def _load_characters(self, pid: uuid.UUID) -> list[dict]:
        result = await self.db.execute(
            select(Character).where(Character.project_id == pid).order_by(Character.sort_order)
        )
        return [row_to_dict(c) for c in result.scalars().all()]

    async def _load_chapters(self, pid: uuid.UUID) -> list[dict]:
        result = await self.db.execute(
            select(Chapter).where(Chapter.project_id == pid).order_by(Chapter.sort_order)
        )
        return [row_to_dict(c) for c in result.scalars().all()]

    async def _load_scenes(self, pid: uuid.UUID) -> list[dict]:
        result = await self.db.execute(
            select(Scene).where(Scene.project_id == pid).order_by(Scene.sort_order)
        )
        return [row_to_dict(s) for s in result.scalars().all()]

    async def _load_contents(self, scenes: list[dict]) -> dict[str, str]:
        contents: dict[str, str] = {}
        sid_strs = [s["id"] for s in scenes if isinstance(s.get("id"), str)]
        if not sid_strs:
            return contents
        sids = [uuid.UUID(sid) for sid in sid_strs]
        result = await self.db.execute(
            select(SceneContent).where(SceneContent.scene_id.in_(sids))
        )
        for sc in result.scalars().all():
            contents[str(sc.scene_id)] = sc.content or ""
        return contents

    # ---- LLM prompt builders ----

    def _build_character_prompt(self, characters, chapters, scenes, contents_map) -> str:
        chapter_title_map = {ch["id"]: ch["title"] for ch in chapters}
        char_lines = []
        for i, c in enumerate(characters):
            lines = [f"角色{i+1} (ID: {c['id']}): 姓名={c.get('name', '')}"]
            if c.get("role"):
                lines.append(f"  类型={c['role']}")
            if c.get("personality"):
                lines.append(f"  性格={c['personality']}")
            if c.get("appearance"):
                lines.append(f"  外貌={c['appearance']}")
            if c.get("background"):
                lines.append(f"  背景={c['background']}")
            if c.get("motivation"):
                lines.append(f"  动机={c['motivation']}")
            char_lines.append("\n".join(lines))

        scene_lines = []
        for j, s in enumerate(scenes):
            chap = chapter_title_map.get(s.get("chapter_id", ""), "")
            lines = [
                f"场景{j+1} (ID: {s['id']}, 章节={chap}):",
                f"  标题={s.get('title', '')}",
                f"  时间={s.get('scene_time', '')}  地点={s.get('setting', '')}",
                f"  POV={s.get('pov_character', '')}",
                f"  概要={s.get('summary', '')}",
            ]
            content = contents_map.get(s["id"], "")
            if content:
                lines.append(f"  内容片段={content[:300]}")
            scene_lines.append("\n".join(lines))

        return (
            "请分析以下故事中的角色一致性。找出角色设定与场景内容之间的不一致问题，"
            "例如角色性格前后矛盾、角色行为不符合其设定、角色能力/知识不一致等。\n\n"
            f"角色设定：\n{chr(10).join(char_lines)}\n\n"
            f"场景内容：\n{chr(10).join(scene_lines)}\n\n"
            '输出JSON格式：{"issues":[{"check_type":"character","severity":"error|warning|info",'
            '"entity_type":"character","entity_id":"...","description":"...",'
            '"suggestion":"...","chapter_id":"...","scene_id":"..."}]}'
        )

    def _build_timeline_prompt(self, chapters, scenes, edges) -> str:
        parts = ["请分析以下故事的时间线一致性。找出时间线逻辑问题，例如时间跳跃不合理、场景顺序错误、时间线矛盾等。"]
        if chapters:
            parts.append("\n章节顺序：")
            for ch in chapters:
                parts.append(
                    f"  章节ID={ch['id']} 标题={ch.get('title', '')} "
                    f"排序={ch.get('sort_order', 0)} 状态={ch.get('status', '')}"
                )
        if scenes:
            parts.append("\n场景列表（按排序顺序）：")
            for s in scenes:
                parts.append(
                    f"  场景ID={s['id']} 标题={s.get('title', '')} "
                    f"章节ID={s.get('chapter_id', '')} 排序={s.get('sort_order', 0)} "
                    f"时间标签={s.get('scene_time', '')} 概要={s.get('summary', '')[:100]}"
                )
        if edges:
            parts.append("\n章节连接关系：")
            for e in edges:
                parts.append(
                    f"  {e.get('source_id', '')} -> {e.get('target_id', '')} "
                    f"类型={e.get('edge_type', '')} 标签={e.get('label', '')}"
                )
        parts.append(
            '\n输出JSON格式：{"issues":[{"check_type":"timeline","severity":"error|warning|info",'
            '"entity_type":"timeline","entity_id":"...","description":"...",'
            '"suggestion":"...","chapter_id":"...","scene_id":"..."}]}'
        )
        return "\n".join(parts)

    def _build_world_prompt(self, global_settings, characters, scenes) -> str:
        parts = ["请分析以下故事的世界观一致性。找出故事元素与世界观设定之间的矛盾之处。"]
        parts.append(f"\n世界观设定：\n{global_settings if global_settings else '(未设定)'}")
        if characters:
            parts.append("\n角色列表：")
            for c in characters:
                parts.append(
                    f"  角色ID={c['id']} 姓名={c.get('name', '')} "
                    f"背景={c.get('background', '')[:100]}"
                )
        if scenes:
            parts.append("\n场景列表：")
            for s in scenes:
                parts.append(
                    f"  场景ID={s['id']} 标题={s.get('title', '')} "
                    f"地点={s.get('setting', '')} 概要={s.get('summary', '')[:100]}"
                )
        parts.append(
            '\n输出JSON格式：{"issues":[{"check_type":"world_rule","severity":"error|warning|info",'
            '"entity_type":"world","entity_id":"...","description":"...",'
            '"suggestion":"...","chapter_id":"...","scene_id":"..."}]}'
        )
        return "\n".join(parts)

    # ---- Rule-based fallbacks ----

    def _rule_characters(self, characters, scenes, chapters) -> list[ConsistencyIssue]:
        issues: list[ConsistencyIssue] = []
        char_names = {c["name"] for c in characters}
        name_count: dict[str, int] = {}
        for c in characters:
            name_count[c["name"]] = name_count.get(c["name"], 0) + 1

        for name, count in name_count.items():
            if count > 1:
                ids = [str(c.get("id", "")) for c in characters if c["name"] == name]
                issues.append(ConsistencyIssue(
                    check_type="character",
                    severity="warning",
                    entity_type="character",
                    entity_id=",".join(filter(None, ids)),
                    description=f"存在 {count} 个同名为「{name}」的角色",
                    suggestion="建议为角色使用不同的名字以示区分",
                ))

        for s in scenes:
            pov = s.get("pov_character", "")
            if pov and pov not in char_names:
                issues.append(ConsistencyIssue(
                    check_type="character",
                    severity="warning",
                    entity_type="character",
                    description=f"场景「{s.get('title', '')}」的 POV 角色「{pov}」未在角色列表中定义",
                    suggestion="请在角色列表中创建该角色，或修改 POV 为已定义角色",
                    scene_id=s.get("id"),
                    chapter_id=s.get("chapter_id"),
                ))

        for c in characters:
            if not c.get("personality") and not c.get("background") and not c.get("motivation"):
                issues.append(ConsistencyIssue(
                    check_type="character",
                    severity="info",
                    entity_type="character",
                    entity_id=c.get("id"),
                    description=f"角色「{c.get('name', '')}」缺少性格、背景和动机描述",
                    suggestion="请补充角色的性格、背景和动机设定",
                ))

        return issues

    def _rule_timeline(self, chapters, scenes) -> list[ConsistencyIssue]:
        issues: list[ConsistencyIssue] = []
        ch_sorted = sorted(chapters, key=lambda x: x.get("sort_order", 0))
        seen_orders: set[int] = set()
        for ch in ch_sorted:
            order = ch.get("sort_order", 0)
            if order in seen_orders:
                issues.append(ConsistencyIssue(
                    check_type="timeline",
                    severity="warning",
                    entity_type="chapter",
                    entity_id=ch.get("id"),
                    description=f"章节「{ch.get('title', '')}」的排序值 {order} 与其他章节重复",
                    suggestion="请为每个章节设置唯一的排序值",
                ))
            seen_orders.add(order)

        ch_scenes: dict[str, list[dict]] = {}
        for s in scenes:
            ch_id = s.get("chapter_id", "")
            if ch_id not in ch_scenes:
                ch_scenes[ch_id] = []
            ch_scenes[ch_id].append(s)

        for ch_id, sc_list in ch_scenes.items():
            sc_sorted = sorted(sc_list, key=lambda x: x.get("sort_order", 0))
            seen: set[int] = set()
            for s in sc_sorted:
                order = s.get("sort_order", 0)
                if order in seen:
                    issues.append(ConsistencyIssue(
                        check_type="timeline",
                        severity="warning",
                        entity_type="scene",
                        entity_id=s.get("id"),
                        description=f"场景「{s.get('title', '')}」的排序值 {order} 在同一章节中重复",
                        suggestion="请为每个场景设置唯一的排序值",
                        chapter_id=ch_id,
                        scene_id=s.get("id"),
                    ))
                seen.add(order)

        return issues

    def _rule_world(self, global_settings, characters, scenes) -> list[ConsistencyIssue]:
        issues: list[ConsistencyIssue] = []
        if not global_settings:
            issues.append(ConsistencyIssue(
                check_type="world_rule",
                severity="info",
                entity_type="world",
                description="项目未设定世界观规则",
                suggestion="在项目设置中补充世界观设定，有助于保持故事一致性",
            ))
            return issues

        for c in characters:
            missing = []
            if not c.get("background"):
                missing.append("背景")
            if not c.get("personality"):
                missing.append("性格")
            if missing:
                issues.append(ConsistencyIssue(
                    check_type="world_rule",
                    severity="info",
                    entity_type="character",
                    entity_id=c.get("id"),
                    description=f"角色「{c.get('name', '')}」缺少{'、'.join(missing)}描述",
                    suggestion=f"请补充角色的{'、'.join(missing)}描述以符合世界观设定",
                ))

        return issues
