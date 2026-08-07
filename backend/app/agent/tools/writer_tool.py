from __future__ import annotations

import logging
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.llm.client import LLMClient
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode, verify_project_owner
from app.agent.context import ContextBuilder
from app.agent.agents.writer_agent import WritingAgent
from app.agent.agents.scene_sync import build_scene_sync_material, run_scene_sync
from app.agent.tools.writing_tools import WriteSceneContentTool
from app.storycad.models import Scene
from sqlalchemy import select

logger = logging.getLogger(__name__)


def _action_label(action: str) -> str:
    return {"continue": "续写", "rewrite": "重写"}.get(action, "创作")


class CallWriterAgentTool(BaseTool):
    """调用专业写作智能体进行场景正文创作。

    工作流程：
    1. 通过 ContextBuilder.build_for_writing() 获取专注的写作上下文
    2. 调用 WritingAgent 生成正文（纯写作 prompt，无工具干扰）
    3. 直接保存到 DB，返回摘要给主 LLM（避免正文文本在主 LLM 上下文中膨胀）
    """

    meta = ToolMeta(
        name="call_writer_agent",
        description="调用专业写作智能体进行场景正文创作，支持新写、续写、重写。完成后自动保存。",
        concurrency=ConcurrencyMode.EXCLUSIVE,
        timeout=180,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {
                    "type": "string",
                    "description": "场景ID，来自 list_scenes 或 read_full_project",
                },
                "action": {
                    "type": "string",
                    "enum": ["write", "continue", "rewrite"],
                    "description": "write=新写覆盖, continue=续写追加, rewrite=重写",
                },
                "instructions": {
                    "type": "string",
                    "description": "写作指导：字数要求、风格方向、重点内容等",
                },
            },
            "required": ["scene_id", "action"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        try:
            scene_id_raw = self._require_param(kwargs, "scene_id")
            if scene_id_raw is None:
                return self._missing_param("scene_id")
            action = kwargs.get("action")
            if action not in ("write", "continue", "rewrite"):
                return ToolResult(
                    success=False,
                    error="参数缺失: 工具需要 action 参数但未提供或取值非法（可选值：write/continue/rewrite）。不要省略该参数，否则将默认覆盖场景正文。",
                    correction_hint="下一步：明确指定 action=write（新写覆盖）/continue（续写追加）/rewrite（重写）后再调用本工具",
                )
            instructions = kwargs.get("instructions", "")

            sc_id = uuid.UUID(scene_id_raw)
            result = await db.execute(select(Scene).where(Scene.id == sc_id))
            scene_obj = result.scalar_one_or_none()
            if not scene_obj:
                return self._not_found("Scene")
            await verify_project_owner(db, scene_obj.project_id, kwargs.get("user_id"))

            # 1. 构建专注的写作上下文
            builder = ContextBuilder(db)
            ctx = await builder.build_for_writing(sc_id, action)

            # 2. 注入指令
            if instructions:
                ctx["instructions"] = instructions
            ctx["action"] = action

            # 3. 调用写作智能体
            agent = WritingAgent()
            user_prompt = f"请{action=='continue' and '续写' or action=='rewrite' and '重写' or '创作'}场景《{ctx.get('scene_title', '')}》的正文。"
            text = await agent.run(self.llm_client, ctx, user_prompt)

            if not text:
                return ToolResult(success=False, error="写作智能体未能生成正文")

            # 4. 保存到 DB
            writer = WriteSceneContentTool(llm_client=self.llm_client)
            save_result = await writer.run(
                db,
                user_id=kwargs.get("user_id"),
                scene_id=scene_id_raw,
                content=text,
            )

            if not save_result.success:
                return save_result

            # 5. 写后同步：蓝图对齐正文 + 质量自评（后端子代理，正文不进主上下文）
            sync_result = await self._sync_scene_blueprint(
                db, sc_id, text, user_id=kwargs.get("user_id"),
            )

            preview = text[:200].replace("\n", " ")
            wc = save_result.data.get("word_count", 0)
            data = {
                "scene_id": scene_id_raw,
                "word_count": wc,
                "action": action,
                "preview": preview,
                "scene_summary": sync_result.summary,
                "quality_score": sync_result.quality_score,
                "quality_brief": sync_result.quality_brief,
                "alignment": sync_result.alignment,
                "suggestion": sync_result.suggestion,
                "issues": sync_result.issues,
                "summary": (
                    f"已{_action_label(action)}场景《{ctx.get('scene_title', '')}》，共 {wc} 字。"
                    f"场景蓝图已同步。自评 {sync_result.quality_score}/10：{sync_result.quality_brief}"
                ),
            }
            correction_hint = (
                "如需调整内容，可以重新调用写作工具，并在指令中说明修改方向"
            )
            if sync_result.alignment == "drifted":
                correction_hint = (
                    "自评判定正文偏离了蓝图意图，建议先调用 sync_scene_blueprint 工具或"
                    "重写该场景，再继续后续写作"
                )
            return ToolResult(success=True, data=data, correction_hint=correction_hint)
        except Exception as e:
            await db.rollback()
            logger.error("CallWriterAgentTool failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=str(e))

    async def _sync_scene_blueprint(
        self,
        db: AsyncSession,
        scene_id: uuid.UUID,
        content: str,
        user_id=None,
    ) -> SceneSyncResult:
        """Run the sync+self-eval pass and persist the updated blueprint.

        Never fails the calling tool on sync problems — the body is already
        saved and the old blueprint stays intact if sync fails.
        """
        try:
            material = await build_scene_sync_material(db, scene_id, content=content)
            if not material:
                return SceneSyncResult(summary="", quality_brief="场景不存在")
            result = await run_scene_sync(self.llm_client, material)
        except Exception as e:
            await db.rollback()
            logger.error("scene blueprint sync failed (scene=%s): %s", scene_id, e, exc_info=True)
            return SceneSyncResult(summary="", quality_brief="蓝图同步异常")
        if result.summary:
            try:
                scene_row = await db.get(Scene, scene_id)
                if scene_row:
                    scene_row.summary = result.summary
                    await db.commit()
                else:
                    logger.warning("_sync_scene_blueprint: scene %s vanished", scene_id)
            except Exception as e:
                await db.rollback()
                logger.error("summary persist failed (scene=%s): %s", scene_id, e, exc_info=True)
        return result


class SyncSceneBlueprintTool(BaseTool):
    """独立蓝图同步工具 — 直接写正文类工具 (write_scene_content etc.) 之后的配套动作。

    在后端子代理中把「创作蓝图」对齐到当前正文并做一条自评，然后把对齐后的蓝图写回
    ``Scene.summary``。正文不会进入主循环上下文。
    """

    meta = ToolMeta(
        name="sync_scene_blueprint",
        description=(
            "同步场景蓝图：读取场景正文（仅在后端子代理中），将对齐后的创作蓝图写回场景概述。"
            "scene_id 来自 list_scenes 或 read_full_project。任何写入正文的操作"
            "（write_scene_content/continue_scene/rewrite_scene/expand_selection/"
            "compress_selection/update_scene）之后都应调用此工具，确保项目读取工具只读蓝图即可继续创作。"
        ),
        concurrency=ConcurrencyMode.EXCLUSIVE,
        timeout=120,
        parameters={
            "type": "object",
            "properties": {
                "scene_id": {
                    "type": "string",
                    "description": "场景ID，来自 list_scenes 或 read_full_project",
                },
                "note": {
                    "type": "string",
                    "description": "本次写入的简要说明（可选），作为自评参考",
                },
            },
            "required": ["scene_id"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        sc_raw = self._require_param(kwargs, "scene_id")
        if sc_raw is None:
            return self._missing_param("scene_id")
        try:
            sc_id = uuid.UUID(sc_raw)
        except (ValueError, TypeError):
            return ToolResult(success=False, error="scene_id 不是合法的 UUID")

        result = await db.execute(select(Scene).where(Scene.id == sc_id))
        scene_obj = result.scalar_one_or_none()
        if not scene_obj:
            return self._not_found("Scene")
        await verify_project_owner(db, scene_obj.project_id, kwargs.get("user_id"))

        try:
            material = await build_scene_sync_material(db, sc_id)
            if not material:
                return self._not_found("Scene")
            if not (material.get("content") or "").strip():
                return ToolResult(
                    success=True,
                    data={
                        "scene_id": sc_raw,
                        "scene_summary": material.get("scene_summary", ""),
                        "note": "场景尚无正文，无需同步",
                    },
                )
            sync = await run_scene_sync(self.llm_client, material)
            if sync.summary and scene_obj.summary != sync.summary:
                scene_obj.summary = sync.summary
                await db.commit()
            return ToolResult(
                success=True,
                data={
                    "scene_id": sc_raw,
                    "scene_summary": sync.summary,
                    "quality_score": sync.quality_score,
                    "quality_brief": sync.quality_brief,
                    "alignment": sync.alignment,
                    "suggestion": sync.suggestion,
                    "issues": sync.issues,
                    "note": f"场景蓝图已同步。自评 {sync.quality_score}/10：{sync.quality_brief}",
                },
            )
        except Exception as e:
            await db.rollback()
            logger.error("SyncSceneBlueprintTool failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=str(e))
