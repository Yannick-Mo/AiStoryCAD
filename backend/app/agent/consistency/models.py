"""Pydantic models for the consistency engine v2.

v2 turns "summarise everything, then look for problems" on its head: each
scene is reduced to a table of *facts* (with verbatim evidence), facts are
grouped per ``(entity, attribute)``, and only attributes carrying more than
one distinct value become *conflict candidates* that an isolated verify
stage judges. These models are the shapes that flow through that pipeline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FactType(str, Enum):
    CHARACTER_STATE = "character_state"
    RELATIONSHIP = "relationship"
    EVENT = "event"
    WORLD_OBJECT = "world_object"
    WORLD_RULE_USE = "world_rule_use"
    META = "meta"


class SourceType(str, Enum):
    SCENE_CONTENT = "scene_content"
    CHARACTER_PROFILE = "character_profile"
    WORLD_SETTINGS = "world_settings"
    SCENE_META = "scene_meta"
    RELATION_DEF = "relation_def"


class Fact(BaseModel):
    """A single extracted fact with verbatim evidence for traceability."""

    entity: str
    attribute: str
    value: str
    evidence: str = ""
    fact_type: FactType = FactType.CHARACTER_STATE
    source_type: SourceType = SourceType.SCENE_CONTENT
    scene_id: str | None = None
    chapter_id: str | None = None
    block_index: int = 0

    def dedup_key(self) -> tuple[str, str, str]:
        """Code-level dedup identity: same entity+attribute+value."""
        return (self.entity.strip(), self.attribute.strip(), self.value.strip())


class ConflictValue(BaseModel):
    """One side of a conflict candidate, with provenance."""

    value: str
    evidence: str = ""
    source_type: SourceType = SourceType.SCENE_CONTENT
    scene_id: str | None = None
    chapter_id: str | None = None


class ConflictCandidate(BaseModel):
    """Same ``(entity, attribute)`` observed with distinct values."""

    entity: str
    attribute: str
    values: list[ConflictValue] = Field(default_factory=list)

    @property
    def distinct_values(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for v in self.values:
            key = v.value.strip()
            if key not in seen:
                seen.add(key)
                out.append(v.value)
        return out


class Verdict(str, Enum):
    """Stage-3 judgement for a conflict candidate."""

    REAL_INCONSISTENCY = "real_inconsistency"
    CHARACTER_DEVELOPMENT = "character_development"
    DISGUISE_DECEPTION = "disguise_deception"
    FLASHBACK_MEMORY = "flashback_memory"
    UNRELIABLE_NARRATOR = "unreliable_narrator"
    METAPHOR_DESCRIPTION = "metaphor_description"
    NEEDS_REVIEW = "needs_review"


class ConsistencyIssue(BaseModel):
    check_type: str
    severity: str
    entity_type: str
    entity_id: str | None = None
    description: str
    suggestion: str | None = None
    chapter_id: str | None = None
    scene_id: str | None = None
    # v2 extras — provenance and judgement from the verify stage.
    verdict: Verdict | None = None
    evidence: list[str] | None = None


class ConsistencyReport(BaseModel):
    project_id: str
    issues: list[ConsistencyIssue]
    summary: str = ""
    timestamp: datetime | None = None
    # v2 extras — pipeline accounting.
    stats: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    def finalize(self, project_id: str | None = None) -> "ConsistencyReport":
        errors = sum(1 for i in self.issues if i.severity == "error")
        warnings = sum(1 for i in self.issues if i.severity == "warning")
        infos = sum(1 for i in self.issues if i.severity == "info")
        if errors == 0 and warnings == 0:
            summary = "未发现一致性问题"
        else:
            summary = f"发现 {errors} 个错误, {warnings} 个警告, {infos} 个提示"
        self.summary = summary
        if project_id is not None:
            self.project_id = project_id
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)
        self.stats = {"errors": errors, "warnings": warnings, "infos": infos}
        return self
