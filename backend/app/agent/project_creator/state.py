# backend/app/agent/project_creator/state.py
import operator
from typing import Annotated, TypedDict


class ChapterDef(TypedDict):
    title: str
    goal: str


class ActDef(TypedDict):
    name: str
    order: int
    color: str
    chapters: list[ChapterDef]


class SceneDef(TypedDict):
    act_idx: int
    chapter_idx: int
    title: str
    pov_character: str
    setting: str
    scene_time: str
    summary: str


class RawCharacter(TypedDict):
    name: str
    description: str


class CharacterDef(TypedDict):
    name: str
    role: str
    personality: str
    appearance: str
    background: str
    motivation: str


class EdgeDef(TypedDict):
    source_act_idx: int
    source_chapter_idx: int
    target_act_idx: int
    target_chapter_idx: int
    type: str  # 'timeline' | 'causal' | 'foreshadow' | 'character'
    label: str


class RelationDef(TypedDict):
    char_name: str
    target_name: str
    rel_type: str
    label: str
    description: str


class MaterialState(TypedDict):
    material: str
    project_title: str
    genre: str
    tone: str
    characters_raw: list[RawCharacter]
    plot_summary: str
    world_elements: str
    acts: list[ActDef]
    estimated_words: int
    scenes: Annotated[list[SceneDef], operator.add]
    characters: list[CharacterDef]
    relations: list[RelationDef]
    edges: list[EdgeDef]
    global_settings: str
    errors: list[str]
