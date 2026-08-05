"""Phase-0 structural rules — deterministic checks that never need an LLM.

These run on *every* check regardless of LLM health and are merged into the
final report alongside LLM-derived issues. Ported and tightened from v1's
``_rule_characters`` / ``_rule_timeline`` / ``_rule_world``.
"""
from __future__ import annotations

from typing import Iterable

from app.agent.consistency.models import ConsistencyIssue


def rule_characters(
    characters: Iterable[dict],
    scenes: Iterable[dict],
) -> list[ConsistencyIssue]:
    issues: list[ConsistencyIssue] = []
    char_names = {c.get("name", "") for c in characters}
    name_groups: dict[str, list[dict]] = {}
    for c in characters:
        name = c.get("name", "")
        if name:
            name_groups.setdefault(name, []).append(c)

    for name, group in name_groups.items():
        if len(group) > 1:
            ids = [str(c.get("id", "")) for c in group if c.get("id")]
            issues.append(
                ConsistencyIssue(
                    check_type="character",
                    severity="warning",
                    entity_type="character",
                    entity_id=ids[0] if ids else None,
                    description=f"存在 {len(group)} 个同名为「{name}」的角色",
                    suggestion="建议为角色使用不同的名字以示区分",
                )
            )

    for s in scenes:
        pov = s.get("pov_character", "")
        if pov and pov not in char_names:
            issues.append(
                ConsistencyIssue(
                    check_type="character",
                    severity="warning",
                    entity_type="character",
                    description=f"场景「{s.get('title', '')}」的 POV 角色「{pov}」未在角色列表中定义",
                    suggestion="请在角色列表中创建该角色，或修改 POV 为已定义角色",
                    scene_id=s.get("id"),
                    chapter_id=s.get("chapter_id"),
                )
            )

    for c in characters:
        if not (c.get("personality") or c.get("background") or c.get("motivation")):
            issues.append(
                ConsistencyIssue(
                    check_type="character",
                    severity="info",
                    entity_type="character",
                    entity_id=c.get("id"),
                    description=f"角色「{c.get('name', '')}」缺少性格、背景和动机描述",
                    suggestion="请补充角色的性格、背景和动机设定",
                )
            )
    return issues


def rule_timeline(
    chapters: Iterable[dict],
    scenes: Iterable[dict],
) -> list[ConsistencyIssue]:
    issues: list[ConsistencyIssue] = []

    seen_orders: set[int] = set()
    for ch in chapters:
        order = ch.get("sort_order", 0)
        if order in seen_orders:
            issues.append(
                ConsistencyIssue(
                    check_type="timeline",
                    severity="warning",
                    entity_type="chapter",
                    entity_id=ch.get("id"),
                    description=f"章节「{ch.get('title', '')}」的排序值 {order} 与其他章节重复",
                    suggestion="请为每个章节设置唯一的排序值",
                )
            )
        seen_orders.add(order)

    scenes_by_chapter: dict[str, list[dict]] = {}
    for s in scenes:
        ch_id = s.get("chapter_id", "")
        scenes_by_chapter.setdefault(ch_id, []).append(s)

    for ch_id, sc_list in scenes_by_chapter.items():
        sc_sorted = sorted(sc_list, key=lambda x: x.get("sort_order", 0))
        seen: set[int] = set()
        for s in sc_sorted:
            order = s.get("sort_order", 0)
            if order in seen:
                issues.append(
                    ConsistencyIssue(
                        check_type="timeline",
                        severity="warning",
                        entity_type="scene",
                        entity_id=s.get("id"),
                        description=f"场景「{s.get('title', '')}」的排序值 {order} 在同一章节中重复",
                        suggestion="请为每个场景设置唯一的排序值",
                        chapter_id=ch_id,
                        scene_id=s.get("id"),
                    )
                )
            seen.add(order)
    return issues


def rule_world(
    global_settings: str,
    characters: Iterable[dict],
) -> list[ConsistencyIssue]:
    issues: list[ConsistencyIssue] = []
    if not global_settings:
        issues.append(
            ConsistencyIssue(
                check_type="world_rule",
                severity="info",
                entity_type="world",
                description="项目未设定世界观规则",
                suggestion="在项目设置中补充世界观设定，有助于保持故事一致性",
            )
        )
        return issues

    for c in characters:
        missing = []
        if not c.get("background"):
            missing.append("背景")
        if not c.get("personality"):
            missing.append("性格")
        if missing:
            issues.append(
                ConsistencyIssue(
                    check_type="world_rule",
                    severity="info",
                    entity_type="character",
                    entity_id=c.get("id"),
                    description=f"角色「{c.get('name', '')}」缺少{'、'.join(missing)}描述",
                    suggestion=f"请补充角色的{'、'.join(missing)}描述以符合世界观设定",
                )
            )
    return issues


def run_structural_rules(
    global_settings: str,
    characters: Iterable[dict],
    chapters: Iterable[dict],
    scenes: Iterable[dict],
) -> list[ConsistencyIssue]:
    return (
        rule_characters(characters, scenes)
        + rule_timeline(chapters, scenes)
        + rule_world(global_settings, characters)
    )
