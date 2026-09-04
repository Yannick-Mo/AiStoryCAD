"""Scene blueprint sync + quality self-evaluation.

Runs fully in the backend: consumes a scene's blueprint and body text,
asks a clean-context LLM to *align the blueprint to reality* and produce
a short quality verdict, then persists the updated blueprint into
``Scene.summary``.  The body text never enters the main loop context —
this is what lets future `read_*` tools serve stories from blueprints
alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import render_prompt
from app.agent.utils import GenerationError, parse_json_safe
from app.llm.client import LLMClient
from app.llm.types import Message
from app.storycad.models import Act, Chapter, Scene, SceneContent

_VALID_ALIGNMENTS = ("aligned", "updated", "drifted")


@dataclass
class SceneSyncResult:
    """Outcome of a blueprint sync + self-review pass."""

    summary: str = ""
    quality_score: int = 5
    quality_brief: str = ""
    alignment: str = "aligned"
    suggestion: str = ""
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.summary.strip())


def _coerce_summary(summary: str) -> str:
    """Normalise a blueprint: strip to text, never invent content."""
    summary = (summary or "").strip()
    if not summary:
        return ""
    return summary


def _clamp_score(score: Any) -> int:
    try:
        score = int(score)
    except (TypeError, ValueError):
        return 5
    return max(0, min(10, score))


async def build_scene_sync_material(
    db: AsyncSession,
    scene_id,
    content: str | None = None,
) -> dict:
    """Collect the inputs the sync agent needs, straight from the DB.

    Loads the scene, its body (unless overridden), chapter, prior scene's
    blueprint (for continuity) and act name.
    """
    result = await db.execute(select(Scene).where(Scene.id == scene_id))
    scene = result.scalar_one_or_none()
    if scene is None:
        return {}

    material: dict[str, Any] = {
        "scene_id": str(scene.id),
        "scene_title": scene.title or "",
        "scene_summary": scene.summary or "",
        "scene_setting": scene.setting or "",
        "scene_time": scene.scene_time or "",
        "pov_character_name": scene.pov_character or "",
    }

    if content is not None:
        material["content"] = content
    else:
        c_result = await db.execute(
            select(SceneContent).where(SceneContent.scene_id == scene.id)
        )
        sc = c_result.scalar_one_or_none()
        material["content"] = sc.content or "" if sc else ""

    ch_result = await db.execute(select(Chapter).where(Chapter.id == scene.chapter_id))
    chapter = ch_result.scalar_one_or_none()
    if chapter is not None:
        material["chapter_title"] = chapter.title or ""
        material["chapter_goal"] = chapter.goal or ""
        if chapter.act_id:
            act_result = await db.execute(select(Act).where(Act.id == chapter.act_id))
            act = act_result.scalar_one_or_none()
            material["act_name"] = act.name if act else ""

        # Previous scene's blueprint — its ending state is this scene's opening.
        prev_result = await db.execute(
            select(Scene)
            .where(
                Scene.chapter_id == chapter.id,
                Scene.sort_order < scene.sort_order,
            )
            .order_by(Scene.sort_order.desc())
            .limit(1)
        )
        prev_scene = prev_result.scalar_one_or_none()
        if prev_scene is not None and prev_scene.summary:
            material["previous_scene_summary"] = prev_scene.summary or ""

    return material


async def run_scene_sync(
    client: LLMClient,
    material: dict,
) -> SceneSyncResult:
    """Run the sync+self-eval pass. Never raises for noisy LLM output.

    On any parse failure the original (``material["scene_summary"]``)
    blueprint is preserved as-is with a neutral verdict, so a broken
    sync can never lose story data.
    """
    system = render_prompt("scene_sync", **material) or ""
    if not (material.get("content") or "").strip():
        return SceneSyncResult(
            summary=material.get("scene_summary", ""),
            quality_brief="场景尚无正文，无需同步",
        )

    messages = [
        Message(role="system", content=system),
        Message(role="user", content="请更新场景蓝图并完成自评。"),
    ]
    try:
        result = await client.chat(
            messages, temperature=0.4, max_tokens=1600,
            response_format="json_object",
        )
        parsed = await parse_json_safe(
            result.content or "", client, messages, node_name="scene_sync"
        )
    except GenerationError as e:
        logger.warning("scene_sync generation failed, keeping original blueprint: %s", e)
        return SceneSyncResult(
            summary=material.get("scene_summary", ""),
            quality_brief="蓝图同步失败，保留原蓝图",
        )
    except Exception as e:
        logger.warning("scene_sync unexpected error, keeping original blueprint: %s", e)
        return SceneSyncResult(
            summary=material.get("scene_summary", ""),
            quality_brief="蓝图同步异常，保留原蓝图",
        )

    summary = _coerce_summary(parsed.get("summary") or material.get("scene_summary", ""))
    alignment = parsed.get("alignment") or "aligned"
    if alignment not in _VALID_ALIGNMENTS:
        alignment = "aligned"
    issues = parsed.get("issues") or []
    if not isinstance(issues, list):
        issues = []
    issues = [str(i) for i in issues][:3]

    return SceneSyncResult(
        summary=summary,
        quality_score=_clamp_score(parsed.get("quality_score")),
        quality_brief=str(parsed.get("quality_brief") or "")[:80],
        alignment=alignment,
        suggestion=str(parsed.get("suggestion") or ""),
        issues=issues,
    )