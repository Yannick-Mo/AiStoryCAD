"""Pure helpers for the fact ledger: normalisation, dedup, conflict discovery.

These are deliberately LLM-free. The only implementation of the value
normalisation semantic lives here (``normalise_value``) and is shared by the
write path (worker), the discovery path (reconcile) and the probe path
(live hints) — never duplicate it.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency.models import (
    ConflictCandidate,
    ConflictValue,
    Fact,
    SourceType,
)
from app.agent.consistency.orm import ConsistencyFact

# Bracketed modifiers dropped by normalisation: （亮）（[修饰]）… — "蓝(亮)" → "蓝".
_BRACKET_RE = re.compile(r"[（(【\[][^（(【\]]*[）)】\]]")
_WS_RE = re.compile(r"\s+")


def normalise_value(v: str) -> str:
    """Normalise a value for conflict discovery (§14.3).

    Rules (mechanic, never semantic):
      1. NFKC fold — full-width 「蓝」「３米」「１２３」 collapse onto their
         half-width forms, so 全角蓝 and 半角蓝 converge to one value.
      2. drop bracketed modifiers — "蓝(亮)" → "蓝".
      3. collapse all whitespace.

    Examples: "蓝 色" → "蓝色"; "蓝(亮)" → "蓝"; "蓝色" stays "蓝色".
    Near-synonyms (深蓝/浅蓝) deliberately *keep* their difference — that
    is judged in the verify stage, not here (宁多勿漏).
    """
    s = unicodedata.normalize("NFKC", v or "").strip()
    s = _BRACKET_RE.sub("", s)
    s = _WS_RE.sub("", s).strip()
    return s


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
        cut = remaining.rfind("\n", 0, block_chars)
        if cut <= 0:
            cut = block_chars
        blocks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return blocks


def scene_meta_facts(scene: dict) -> list[Fact]:
    """Produce lightweight facts from scene metadata (no LLM required)."""
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
    """Merge facts identical on ``(entity, attribute, value)``."""
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
    """v2 discovery helper (kept for tests/back-compat). v3 discovery is the
    SQL-side cluster enumeration in :mod:`app.agent.consistency.reconcile`;
    this remains the pure in-memory equivalent."""
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

    The model may return anything; only well-formed rows are kept.
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


def dedupe_pairs(pairs: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
    """``(entity, attribute, value_a, value_b)`` dictionary-order converge + dedupe.

    Implements §4's value-pair convergence: the pair (a,b) is always written
    as the lexicographically smaller value in slot a, so a later discovery
    with the two values swapped never creates a second row.
    """
    seen: set[tuple[str, str, str, str]] = set()
    out: list[tuple[str, str, str, str]] = []
    for entity, attribute, va, vb in pairs:
        if va == vb:
            continue
        a, b = (va, vb) if va < vb else (vb, va)
        key = (entity.strip(), attribute.strip(), a, b)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


async def insert_facts_for_scene(
    db: AsyncSession,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
    chapter_id: uuid.UUID | None,
    rows: list[dict],
) -> dict:
    """Deactivate old fact rows for the scene, then insert the new ones.

    ``rows`` items: ``{"entity", "attribute", "value", "value_norm",
    "evidence", "source_type", "entity_vec", "value_vec"}`` (vectors optional).

    Deactivation and insertion run in one transaction (caller commits).
    Deactivation runs *even when rows is empty* (cleared-scene semantics,
    §5.3 step 5) — the two are deliberately decoupled; a failed extraction
    must leave the old snapshot alone, which the caller achieves by not
    calling this at all on failure.
    """
    deactivated = await _deactivate_scene_facts(db, project_id, scene_id)
    inserted = 0
    for r in rows:
        db.add(
            ConsistencyFact(
                project_id=project_id,
                scene_id=scene_id,
                chapter_id=chapter_id,
                entity=r["entity"],
                entity_vec=r.get("entity_vec"),
                attribute=r["attribute"],
                value=r["value"],
                value_norm=r.get("value_norm") or normalise_value(r["value"]),
                value_vec=r.get("value_vec"),
                evidence=r.get("evidence", "")[:200],
                source_type=r.get("source_type", "scene_content"),
                is_active=True,
            )
        )
        inserted += 1
    return {"inserted": inserted, "deactivated": deactivated}


async def deactivate_scene_facts(
    db: AsyncSession,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
) -> int:
    """Share the same SQLAlchemy ORM path as the insert helper."""
    return await _deactivate_scene_facts(db, project_id, scene_id)


async def _deactivate_scene_facts(
    db: AsyncSession,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
) -> int:
    result = await db.execute(
        ConsistencyFact.__table__.update()
        .where(
            ConsistencyFact.project_id == project_id,
            ConsistencyFact.scene_id == scene_id,
            ConsistencyFact.is_active.is_(True),
        )
        .values(is_active=False)
    )
    return result.rowcount or 0


async def find_cluster_candidates(
    db: AsyncSession,
    project_id: uuid.UUID,
    scene_id: uuid.UUID | None = None,
) -> list[tuple[str, str, str, str, str, str]]:
    """Cluster discovery over active facts (shared by probe + reconcile).

    Returns ``[(entity, attribute, value_norm, evidence, scene_id, chapter_id), ...]``.
    With *scene_id* set, only facts of that scene are considered (the
    write-time probe gathers those for the incoming batch); without it the
    whole project's active ledger is scanned (§14.3).
    """
    stmt = (
        select(
            ConsistencyFact.entity,
            ConsistencyFact.attribute,
            ConsistencyFact.value_norm,
            ConsistencyFact.evidence,
            ConsistencyFact.scene_id,
            ConsistencyFact.chapter_id,
        )
        .where(
            ConsistencyFact.project_id == project_id,
            ConsistencyFact.is_active.is_(True),
        )
    )
    if scene_id is not None:
        stmt = stmt.where(ConsistencyFact.scene_id == scene_id)
    result = await db.execute(stmt)
    return [
        (row.entity, row.attribute, row.value_norm or "", row.evidence or "", str(row.scene_id), str(row.chapter_id or ""))
        for row in result.all()
    ]