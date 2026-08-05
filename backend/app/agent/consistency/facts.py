"""Pure helpers for the fact pipeline: chunking, dedup, conflict discovery.

These are deliberately LLM-free. The engine calls them between LLM stages
so that no token is spent on operations the code can do exactly.
"""
from __future__ import annotations

from collections import defaultdict

from app.agent.consistency.models import (
    ConflictCandidate,
    ConflictValue,
    Fact,
    SourceType,
)


def chunk_text(text: str, block_chars: int) -> list[str]:
    """Split *text* into blocks no longer than *block_chars*.

    Prefers to break on line boundaries so prompts stay readable; falls back
    to hard slicing for pathological input. Empty text yields an empty list.
    """
    if not text:
        return []
    blocks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= block_chars:
            blocks.append(remaining)
            break
        # Try to cut at the last newline before the limit.
        cut = remaining.rfind("\n", 0, block_chars)
        if cut <= 0:
            cut = block_chars
        blocks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return blocks


def scene_meta_facts(scene: dict) -> list[Fact]:
    """Produce lightweight facts from scene metadata (no LLM required).

    These cover small scenes whose body is too short to warrant an extraction
    call, and anchor every scene with its declared time/location.
    """
    scene_id = scene.get("id")
    chapter_id = scene.get("chapter_id")
    facts: list[Fact] = []
    for attr, val in (
        ("所在地", scene.get("setting", "")),
        ("时间标签", scene.get("scene_time", "")),
        ("POV", scene.get("pov_character", "")),
    ):
        if not val:
            continue
        facts.append(
            Fact(
                entity=scene.get("title", "") or scene_id or "",
                attribute=attr,
                value=val,
                fact_type="meta",
                source_type=SourceType.SCENE_META,
                scene_id=scene_id,
                chapter_id=chapter_id,
                evidence=f"场景元数据：{attr}={val}",
            )
        )
    return facts


def dedup_facts(facts: list[Fact]) -> list[Fact]:
    """Merge facts identical on ``(entity, attribute, value)``.

    Evidence strings are joined so no source is lost; the scene/chapter of
    the first occurrence is kept.
    """
    merged: dict[tuple[str, str, str], Fact] = {}
    order: list[tuple[str, str, str]] = []
    for f in facts:
        key = f.dedup_key()
        if not key[0]:
            continue
        if key in merged:
            existing = merged[key]
            ev = [e for e in (existing.evidence, f.evidence) if e]
            if ev:
                existing.evidence = "；".join(ev)
        else:
            merged[key] = f.model_copy(deep=True)
            order.append(key)
    return [merged[k] for k in order]


def group_by_entity_attribute(facts: list[Fact]) -> dict[tuple[str, str], list[Fact]]:
    groups: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    for f in facts:
        groups[(f.entity.strip(), f.attribute.strip())].append(f)
    return groups


def find_conflicts(facts: list[Fact]) -> list[ConflictCandidate]:
    """Discover conflict candidates: a ``(entity, attribute)`` holding more
    than one distinct value. 'Distinct' is exact-match after stripping — a
    near-miss like 蓝色 vs 深蓝 is deliberately *kept* and left for the
    verify stage (宁多勿漏)."""
    candidates: list[ConflictCandidate] = []
    for (entity, attribute), group in group_by_entity_attribute(facts).items():
        distinct: dict[str, Fact] = {}
        for f in group:
            key = f.value.strip()
            if key and key not in distinct:
                distinct[key] = f
        if len(distinct) > 1:
            values = [
                ConflictValue(
                    value=f.value,
                    evidence=f.evidence,
                    source_type=f.source_type,
                    scene_id=f.scene_id,
                    chapter_id=f.chapter_id,
                )
                for f in distinct.values()
            ]
            candidates.append(ConflictCandidate(entity=entity, attribute=attribute, values=values))
    return candidates


def facts_from_extraction(
    payload: dict,
    scene_id: str | None,
    chapter_id: str | None,
    block_index: int = 0,
    source_type: SourceType = SourceType.SCENE_CONTENT,
) -> list[Fact]:
    """Validate/normalize a stage-1 extraction payload into Facts.

    The model may return anything; only well-formed rows are kept, the rest
    are ignored (extraction quality is judged downstream, not here).
    """
    raw_items = payload.get("facts") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return []
    facts: list[Fact] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        entity = str(item.get("entity", "")).strip()
        attribute = str(item.get("attribute", "")).strip()
        value = str(item.get("value", "")).strip()
        if not (entity and attribute and value):
            continue
        try:
            facts.append(
                Fact(
                    entity=entity,
                    attribute=attribute,
                    value=value,
                    evidence=str(item.get("evidence", ""))[:200],
                    fact_type=item.get("fact_type", "character_state"),
                    source_type=source_type,
                    scene_id=scene_id,
                    chapter_id=chapter_id,
                    block_index=block_index,
                )
            )
        except ValueError:
            # Invalid fact_type/source_type enum — drop the row rather than fail.
            facts.append(
                Fact(
                    entity=entity,
                    attribute=attribute,
                    value=value,
                    evidence=str(item.get("evidence", ""))[:200],
                    source_type=source_type,
                    scene_id=scene_id,
                    chapter_id=chapter_id,
                    block_index=block_index,
                )
            )
    return facts
