"""延迟列（deferred column）的结构性保证：正文永不泄漏进列表。

``chapters.content`` 是 deferred 列。这不是省事，而是**结构性保证**：
列出章节的查询不得把正文拖进内存，也不能退化成 N+1 次懒加载。
落地方式是 :func:`app.utils.row_to_dict` 按 mapper 检查 deferred 标记并跳过该列，
而不是靠每个调用点自觉不写正文。

实测过的事故：row_to_dict 原本遍历 ``__table__.columns`` 并 ``getattr`` 每一列，
而 getattr 一个 deferred 列会触发单行懒加载，于是每一次章节列表查询都退化成
N+1 次正文读取。
"""
import uuid

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.project.repository import ProjectRepository
from app.storycad.models import Act, Chapter
from app.storycad.repository import AiStoryCADRepository, with_chapter_content
from app.utils import deferred_columns, row_to_dict


BODY = "第一章正文。" * 50


async def _project_with_chapter(db_session: AsyncSession, test_user: dict):
    project = await ProjectRepository(db_session).create("Deferred", "", test_user["id"])
    act = Act(project_id=project.id, name="第一幕", purpose="第一幕要干什么", sort_order=1)
    db_session.add(act)
    await db_session.flush()
    chapter = Chapter(project_id=project.id, act_id=act.id, title="第一章",
                      goal="本章要写什么", result="本章写成了什么",
                      content=BODY, status="draft", sort_order=1)
    db_session.add(chapter)
    await db_session.flush()
    return project, act, chapter


# ── mapper 层：只有 content 是延迟列 ──────────────────────────────────────────

def test_content_is_the_only_deferred_column_on_chapter():
    assert deferred_columns(Chapter()) == frozenset({"content"})


def test_non_deferred_models_have_no_deferred_columns():
    for obj in (Act(),):
        assert deferred_columns(obj) == frozenset()


# ── row_to_dict：列表载荷里没有 content ───────────────────────────────────────

async def test_row_to_dict_omits_chapter_content(db_session: AsyncSession, test_user: dict):
    _project, _act, chapter = await _project_with_chapter(db_session, test_user)
    row = row_to_dict(chapter)

    assert "content" not in row, "正文泄漏进了章节载荷——列表查询会拖上全文"
    # 其余列一个都不少
    assert row["title"] == "第一章"
    assert row["goal"] == "本章要写什么"
    assert row["result"] == "本章写成了什么"
    assert row["status"] == "draft"
    assert row["id"] == str(chapter.id)
    expected = {c.name for c in Chapter.__table__.columns} - {"content"}
    assert set(row) == expected


async def test_editor_data_never_carries_body_text(db_session: AsyncSession, test_user: dict):
    """编辑器全量加载也走 row_to_dict —— 正文依旧不在其中。"""
    project, _act, _chapter = await _project_with_chapter(db_session, test_user)
    data = await AiStoryCADRepository(db_session).get_editor_data(project.id)

    assert len(data["chapters"]) == 1
    assert "content" not in data["chapters"][0]


async def test_list_entities_never_carries_body_text(db_session: AsyncSession, test_user: dict):
    project, _act, _chapter = await _project_with_chapter(db_session, test_user)
    rows = await AiStoryCADRepository(db_session).list_entities(Chapter, project.id)

    assert rows
    assert "content" not in rows[0]


# ── 正文本身没丢：显式读取（undefer）拿得到 ───────────────────────────────────

async def test_plain_select_leaves_content_unloaded(db_session: AsyncSession, test_user: dict):
    _project, act, _chapter = await _project_with_chapter(db_session, test_user)
    act_id = act.id
    db_session.expunge_all()

    fresh = (await db_session.execute(
        select(Chapter).where(Chapter.act_id == act_id)
    )).scalars().one()
    assert "content" in sa_inspect(fresh).unloaded, "正文被顺手拖了出来"


async def test_content_is_readable_when_explicitly_undeferred(db_session: AsyncSession, test_user: dict):
    _project, act, chapter = await _project_with_chapter(db_session, test_user)
    _act_id, chapter_id = act.id, chapter.id
    db_session.expunge_all()

    fresh = (await db_session.execute(
        with_chapter_content(select(Chapter).where(Chapter.id == chapter_id))
    )).scalars().one()

    assert sa_inspect(fresh).unloaded == set()
    assert fresh.content == BODY, "正文读不出来了——延迟加载把数据弄丢了"


async def test_repository_content_accessors_round_trip(db_session: AsyncSession, test_user: dict):
    project, _act, chapter = await _project_with_chapter(db_session, test_user)
    repo = AiStoryCADRepository(db_session)

    assert await repo.get_chapter_content(chapter.id, project.id) == BODY
    contents = await repo.get_all_chapter_contents(project.id)
    assert contents[str(chapter.id)] == BODY

    await repo.save_chapter_content(chapter.id, project.id, "改过的正文")
    assert await repo.get_chapter_content(chapter.id, project.id) == "改过的正文"


async def test_get_chapter_content_distinguishes_missing_chapter(db_session: AsyncSession, test_user: dict):
    """章不存在 → None；章存在但正文空 → ""。调用方据此返回 404。"""
    project, _act, _chapter = await _project_with_chapter(db_session, test_user)
    repo = AiStoryCADRepository(db_session)

    assert await repo.get_chapter_content(uuid.uuid4(), project.id) is None
    assert await repo.get_chapter_content(uuid.uuid4(), uuid.uuid4()) is None
