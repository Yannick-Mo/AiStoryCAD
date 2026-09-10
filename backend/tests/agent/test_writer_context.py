"""build_for_writing — the focused context handed to the writing agent.

The chapter framework lists every scene of the chapter so the writer knows the
shape of the whole chapter; each entry carries that scene's blueprint.
"""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ContextBuilder
from app.project.repository import ProjectRepository
from app.storycad.repository import AiStoryCADRepository


pytestmark = pytest.mark.asyncio


# 超过 200 字的蓝图，尾部埋一个标记：框架里还看得到它，就说明没有被截断
LONG_SUMMARY = (
    "【目标】把柳家登门的退婚戏演成荒诞喜剧。"
    + "铺垫" * 80
    + "\n【节拍】1.柳父登门；2.呈上书信；3.主角抢话同意。"
    + "\n【结尾状态】灵石到手 —— 尾标记=BLUEPRINT_TAIL_MARKER"
)


async def _project(db_session: AsyncSession, test_user: dict):
    repo = AiStoryCADRepository(db_session)
    project = await ProjectRepository(db_session).create("WriterCtx", "", test_user["id"])
    act_id = str(uuid.uuid4())
    await repo.sync_editor_data(project.id, {"acts": {"created": [
        {"id": act_id, "name": "第一幕", "sort_order": 1}
    ]}})
    chapter_id = str(uuid.uuid4())
    await repo.sync_editor_data(project.id, {"chapters": {"created": [
        {"id": chapter_id, "act_id": act_id, "title": "第一章", "goal": "章蓝图"}
    ]}})
    return project, repo, chapter_id


async def _add_scene(repo: AiStoryCADRepository, project_id, chapter_id, title, summary) -> str:
    scene_id = str(uuid.uuid4())
    await repo.sync_editor_data(project_id, {"scenes": {"created": [
        {"id": scene_id, "chapter_id": chapter_id, "title": title,
         "summary": summary, "pov_character": "林青风"}
    ]}})
    return scene_id


async def test_framework_carries_every_scene_blueprint_in_full(
    db_session: AsyncSession, test_user: dict
):
    project, repo, chapter_id = await _project(db_session, test_user)
    await _add_scene(repo, project.id, chapter_id, "开场", "【目标】冷启动")
    await _add_scene(repo, project.id, chapter_id, "退婚", LONG_SUMMARY)
    current = await _add_scene(repo, project.id, chapter_id, "字据", "【目标】收尾")

    ctx = await ContextBuilder(db_session).build_for_writing(uuid.UUID(current), "write")
    framework = ctx["chapter_scenes_framework"]

    assert "BLUEPRINT_TAIL_MARKER" in framework, "蓝图第 200 字之后的内容被截断了"
    assert "【节拍】1.柳父登门；2.呈上书信；3.主角抢话同意。" in framework
    assert "- **1. 开场**" in framework
    assert "- **2. 退婚**" in framework
    assert "- **3. 字据**" in framework
    assert "← 当前场景" in framework
    assert "→ 下一场" not in framework, "当前是最后一场，不应该有下一场标记"
    assert ctx["chapter_number"] == 1


async def test_multi_line_blueprint_stays_inside_its_list_item(
    db_session: AsyncSession, test_user: dict
):
    """多行蓝图要整体缩进，否则第二行会掉出列表项、被当成新条目。"""
    project, repo, chapter_id = await _project(db_session, test_user)
    await _add_scene(repo, project.id, chapter_id, "A", "【目标】一")
    current = await _add_scene(repo, project.id, chapter_id, "B", LONG_SUMMARY)

    ctx = await ContextBuilder(db_session).build_for_writing(uuid.UUID(current), "write")

    for line in ctx["chapter_scenes_framework"].splitlines():
        if not line.strip():
            continue
        assert line.startswith("- **") or line.startswith("  "), \
            f"蓝图行跑出了列表项：{line!r}"