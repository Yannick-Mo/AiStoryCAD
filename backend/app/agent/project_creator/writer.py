"""把流水线的最终状态写进数据库：项目 / 幕 / 章 / 人物 / 角色关系。

契约（调用方按这个用）::

    async for _ in run_pipeline(state):
        pass
    result = await write_project_from_state(db, state, owner_id)   # owner_id: uuid.UUID
    await db.commit()

* ``result`` = ``{"project_id", "act_count", "chapter_count", "character_count",
  "relation_count", "truncated"}``；``truncated`` 是中文清单，列出流水线主动丢掉的
  东西（流水线 validate 记的 ``truncation`` + 这里自己丢掉/截短的），没丢就是 ``[]``。
* **只 flush，从不 commit**：提交由调用方决定，所以中途出错不会留下半个项目——
  调用方 rollback 即可，本模块不会先替你提交一半。
* 只写 项目 / 幕 / 章 / 人物 / 角色关系 这 5 张表（伏笔不由素材流水线生成）；
  场景表、章间连线表、项目配置表、版本快照表都已经不存在，这里没有任何引用。

顺序规则（规格第 5 节）：幕的 sort_order 项目内密集从 1 开始；
章的 sort_order **幕内**密集从 1 开始，章的 act_id 由章自己的 act_index 决定。
act_index 越界的章不会崩、也不会被悄悄挪到别的幕——直接跳过并写进 ``truncated``。
"""
import uuid

from loguru import logger

from app.project.models import Project
from app.storycad.models import Act, Chapter, Character, CharacterRelation
from app.agent.project_creator.state import ROLE_VALUES, as_int, as_text, normalize_role

# 幕的颜色（仅供前端显示）。状态里的幕只有 name / purpose，颜色在这里按序分配。
ACT_COLORS = [
    "#f97316", "#8b5cf6", "#06b6d4", "#ec4899",
    "#10b981", "#eab308", "#3b82f6", "#a855f7",
]

DEFAULT_TITLE = "未命名项目"
DEFAULT_TOTAL_WORDS = 100000
DEFAULT_REL_TYPE = "关联"

# 数据库列宽（超了 asyncpg 会直接报错，所以先截短并如实记账）
TITLE_LIMIT = 255
GENRE_LIMIT = 100
ACT_NAME_LIMIT = 100
CHAPTER_TITLE_LIMIT = 200
CHARACTER_NAME_LIMIT = 100
REL_TYPE_LIMIT = 30
REL_LABEL_LIMIT = 100


def _clip(value: str, limit: int, notes: list[str], what: str) -> str:
    """按数据库列宽截短；真的截了就往 notes 里记一笔（不静默）。"""
    if len(value) <= limit:
        return value
    notes.append(f"{what}超过 {limit} 字（原文 {len(value)} 字），已截短后入库")
    return value[:limit]


def _as_uuid(value) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def write_project_from_state(db, state: dict, owner_id) -> dict:
    """写一个完整项目，返回各项数量与被丢弃数据清单。不提交事务。"""
    state = state if isinstance(state, dict) else {}
    owner_id = _as_uuid(owner_id)
    truncated: list[str] = [str(n) for n in (state.get("truncation") or []) if str(n).strip()]

    # ---------- 项目 ----------
    total_words = as_int(state.get("estimated_words"), 0)
    if total_words <= 0:
        total_words = DEFAULT_TOTAL_WORDS

    project = Project(
        owner_id=owner_id,
        title=_clip(as_text(state.get("project_title")) or DEFAULT_TITLE, TITLE_LIMIT, truncated, "书名"),
        genre=_clip(as_text(state.get("genre")), GENRE_LIMIT, truncated, "类型"),
        logline=as_text(state.get("logline")),
        ending=as_text(state.get("ending")),
        spine=as_text(state.get("spine")),
        global_settings=as_text(state.get("global_settings")),
        total_words=total_words,
        # description 是历史字段（创建对话框也在写），这里沿用旧行为放情节概要以避免丢信息
        description=as_text(state.get("plot_summary")),
    )
    db.add(project)
    await db.flush()
    project_id = project.id

    # ---------- 幕 ----------
    # act_id_by_index 的键是**原状态列表里的下标**：章的 act_index 指的是那个下标，
    # 所以被跳过的幕必须留一个空位，不能重新编号。
    act_id_by_index: dict[int, Act] = {}
    act_count = 0
    for index, raw_act in enumerate(state.get("acts") or []):
        if not isinstance(raw_act, dict):
            truncated.append(f"第{index + 1}幕的数据不是字典，已跳过")
            continue
        act_count += 1
        name = _clip(
            as_text(raw_act.get("name")) or f"第{act_count}幕",
            ACT_NAME_LIMIT, truncated, f"第{act_count}幕的幕名",
        )
        act = Act(
            project_id=project_id,
            name=name,
            purpose=as_text(raw_act.get("purpose")),
            sort_order=act_count,  # 项目内密集从 1 开始
            color=ACT_COLORS[(act_count - 1) % len(ACT_COLORS)],
        )
        db.add(act)
        act_id_by_index[index] = act
    await db.flush()

    # ---------- 章 ----------
    chapter_count = 0
    chapters_in_act: dict[int, int] = {}
    for raw_chapter in state.get("chapters") or []:
        if not isinstance(raw_chapter, dict):
            truncated.append("有一条章数据不是字典，已跳过")
            continue

        act_index = as_int(raw_chapter.get("act_index"), -1)
        title = as_text(raw_chapter.get("title"))
        goal = as_text(raw_chapter.get("goal"))
        display = title or "未命名"

        act = act_id_by_index.get(act_index)
        if act is None:
            if act_id_by_index:
                truncated.append(
                    f"章《{display}》的 act_index={act_index} 不在 0..{len(act_id_by_index) - 1} 之内，已跳过"
                )
            else:
                truncated.append(f"章《{display}》没有可归属的幕（项目里没有任何幕），已跳过")
            continue

        if not title and not goal:
            truncated.append("有一章既没有章名也没有章蓝图，已跳过")
            continue

        chapter_count += 1
        if not title:
            title = f"第{chapter_count}章"
        title = _clip(title, CHAPTER_TITLE_LIMIT, truncated, f"第{chapter_count}章的章名")

        chapters_in_act[act_index] = chapters_in_act.get(act_index, 0) + 1
        db.add(Chapter(
            project_id=project_id,
            act_id=act.id,
            title=title,
            goal=goal,
            sort_order=chapters_in_act[act_index],  # 幕内密集从 1 开始
            status="draft",
        ))
    await db.flush()

    # ---------- 人物 ----------
    character_by_name: dict[str, Character] = {}
    character_count = 0
    for raw_character in state.get("characters") or []:
        if not isinstance(raw_character, dict):
            truncated.append("有一条人物数据不是字典，已跳过")
            continue

        name = as_text(raw_character.get("name"))
        if not name:
            truncated.append("有一个人物没有姓名，已跳过（人物必须有名字才能建行）")
            continue
        name = _clip(name, CHARACTER_NAME_LIMIT, truncated, "人物姓名")

        role = normalize_role(raw_character.get("role"))
        if role not in ROLE_VALUES:  # 兜底：normalize_role 已经保证不会走到这里
            role = "supporting"

        character_count += 1
        character = Character(
            project_id=project_id,
            name=name,
            role=role,
            personality=as_text(raw_character.get("personality")),
            appearance=as_text(raw_character.get("appearance")),
            background=as_text(raw_character.get("background")),
            motivation=as_text(raw_character.get("motivation")),
            arc=as_text(raw_character.get("arc")),
            sort_order=character_count,
        )
        db.add(character)
        if name in character_by_name:
            truncated.append(f"人物「{name}」重名出现多次，角色关系一律指向第一个同名人物")
        else:
            character_by_name[name] = character
    await db.flush()

    # ---------- 角色关系 ----------
    relation_count = 0
    for raw_relation in state.get("relations") or []:
        if not isinstance(raw_relation, dict):
            truncated.append("有一条角色关系数据不是字典，已跳过")
            continue

        # from_name / to_name 是契约字段；旧字段名只在模型偶发写回时兜一下
        from_name = as_text(raw_relation.get("from_name")) or as_text(raw_relation.get("char_name"))
        to_name = as_text(raw_relation.get("to_name")) or as_text(raw_relation.get("target_name"))
        if not from_name or not to_name:
            truncated.append(
                f"角色关系「{from_name or '?'} → {to_name or '?'}」缺少一端人物名，已跳过"
            )
            continue
        if from_name == to_name:
            truncated.append(f"角色关系「{from_name} → {from_name}」两端是同一人，已跳过")
            continue

        source = character_by_name.get(from_name)
        target = character_by_name.get(to_name)
        if source is None or target is None:
            unknown = from_name if source is None else to_name
            truncated.append(
                f"角色关系「{from_name} → {to_name}」引用了不存在的人物「{unknown}」，已跳过"
            )
            continue

        rel_type = _clip(as_text(raw_relation.get("rel_type")), REL_TYPE_LIMIT, truncated, "关系类型")
        db.add(CharacterRelation(
            project_id=project_id,
            character_id=source.id,
            target_id=target.id,
            rel_type=rel_type or DEFAULT_REL_TYPE,
            label=_clip(as_text(raw_relation.get("label")), REL_LABEL_LIMIT, truncated, "关系标签"),
            description=as_text(raw_relation.get("description")),
        ))
        relation_count += 1
    await db.flush()

    if truncated:
        logger.warning(
            "write_project_from_state: 本次写库有 {} 项数据没有完整落地：{}",
            len(truncated), truncated,
        )

    return {
        "project_id": str(project_id),
        "act_count": act_count,
        "chapter_count": chapter_count,
        "character_count": character_count,
        "relation_count": relation_count,
        "truncated": truncated,
    }
