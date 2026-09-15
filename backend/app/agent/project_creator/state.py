# backend/app/agent/project_creator/state.py
"""素材建项目流水线的状态定义与取值归一化。

流水线最终产出的键（= ``writer.write_project_from_state`` 的输入契约）：

    material / project_title / genre / tone / plot_summary / world_elements /
    global_settings / ending / spine / logline / acts / chapters / characters /
    relations / errors

另有三个**流水线内部**使用的键，不属于上面的契约：

    characters_raw   analyze 从素材里抽出的原始人物线索，只给 design_characters 读
    estimated_words  plan_structure 估的目标字数，写库时落到 projects.total_words
    truncation       validate 记下的「被主动丢掉的数据」，writer 会把它并进返回的 truncated

场景层与章间连线已经随这次重构删掉：这里不再有它们的 TypedDict，
章就是最底层，也是正文层（见 docs/故事模型设计规格.md 第 2、3 节）。
"""
from typing import TypedDict

ROLE_VALUES = ("main", "supporting", "minor")

# 模型可能写出来的各种说法 → 规格里的三个定位。定位说的是"戏份轻重"，
# 不是正邪：反派和主角一样是 main。
_ROLE_ALIASES = {
    "main": "main", "protagonist": "main", "lead": "main", "hero": "main",
    "主角": "main", "主人公": "main", "反派": "main", "antagonist": "main", "villain": "main",
    "supporting": "supporting", "support": "supporting", "supporting role": "supporting",
    "ally": "supporting", "mentor": "supporting", "major": "supporting",
    "配角": "supporting", "盟友": "supporting", "导师": "supporting", "次要角色": "supporting",
    "minor": "minor", "minor character": "minor", "cameo": "minor",
    "龙套": "minor", "路人": "minor",
}


def as_text(value) -> str:
    """把模型给的值收敛成字符串：None → ""，标量转字符串，结构化数据当空。

    结构化数据（list / dict）落在文本字段上没有意义，所以返回 ""——
    空了会在 validate 里被如实记一笔，而不是塞一段 JSON 进去。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def as_int(value, default: int = 0) -> int:
    """把模型给的值收敛成整数；认不出来就返回 default。"""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def normalize_role(value) -> str:
    """把模型写的定位收敛到 main / supporting / minor；认不出来的一律 supporting。"""
    return _ROLE_ALIASES.get(as_text(value).lower(), "supporting")


class ActDef(TypedDict):
    name: str      # 幕名
    purpose: str   # 这一幕承担什么转变


class ChapterDef(TypedDict):
    title: str      # 章名
    goal: str       # 本章要写什么：一段连贯的话（章蓝图），不是字段清单
    act_index: int  # 0-based，指向 acts 里的第几幕


class RawCharacter(TypedDict):
    name: str
    description: str


class CharacterDef(TypedDict):
    name: str
    role: str          # main / supporting / minor
    personality: str
    appearance: str
    background: str
    motivation: str    # 他想要什么
    arc: str           # 他从哪到哪


class RelationDef(TypedDict):
    from_name: str
    to_name: str
    rel_type: str
    label: str
    description: str


class MaterialState(TypedDict):
    material: str
    project_title: str
    genre: str
    tone: str
    plot_summary: str
    world_elements: str
    global_settings: str
    ending: str
    spine: str
    logline: str
    acts: list[ActDef]
    chapters: list[ChapterDef]
    characters: list[CharacterDef]
    relations: list[RelationDef]
    errors: list[str]
    # ---- 以下三个是流水线内部键，不属于 writer 的输入契约 ----
    characters_raw: list[RawCharacter]
    estimated_words: int
    truncation: list[str]
