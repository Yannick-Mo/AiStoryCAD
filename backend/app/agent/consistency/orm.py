"""SQLAlchemy ORM models for the consistency engine v3 persistence layer.

The v3 design (一致性分析引擎v3设计文档 §4) replaces the per-scene cache with
a *write-time fact ledger*:

  * ``consistency_facts``         — per-scene extraction product
  * ``entity_aliases``            — alias resolution table (auto/manual)
  * ``conflict_candidates``       — judgement records anchored on value pairs
  * ``consistency_time_cache``    — project-level time-expression ordering
  * ``consistency_fact_queue``    — write-path task queue (persistent)

``consistency_reports`` / ``consistency_logs`` are kept from v2 (migration
0013); ``scene_fact_cache`` is fully abandoned and dropped in migration 0016.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.project.models import Base


class ConsistencyFact(Base):
    """One row of the fact ledger — the product of one scene extraction."""

    __tablename__ = "consistency_facts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    scene_id = Column(UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id = Column(UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True)
    entity = Column(String, nullable=False)
    attribute = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    value_norm = Column(Text, nullable=False)
    evidence = Column(Text, nullable=False)
    source_type = Column(String(30), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("now()"))

    __table_args__ = (
        Index("ix_consistency_facts_proj_active_ent_attr", "project_id", "is_active", "entity", "attribute"),
        Index("ix_consistency_facts_scene", "scene_id", postgresql_where=text("is_active")),
    )


class ConflictCandidateRecord(Base):
    """A judgement record anchored on a normalised value pair.

    Survival is driven by reconciliation: ``last_seen_at`` refreshes while
    the pair still coexists among active facts; ``pending`` rows whose pair
    disappears are archived (evidence snapshot is display-only).
    """

    __tablename__ = "conflict_candidates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    entity = Column(String, nullable=False)
    attribute = Column(String, nullable=False)
    value_a = Column(Text, nullable=False)
    value_b = Column(Text, nullable=False)
    status = Column(String(12), nullable=False, default="pending", server_default="pending")
    verdict = Column(String(30), nullable=True)
    severity = Column(String(10), nullable=True)
    explanation = Column(Text, nullable=True)
    evidence_a = Column(Text, nullable=True)
    evidence_b = Column(Text, nullable=True)
    scene_a = Column(UUID(as_uuid=True), nullable=True)
    chapter_a = Column(UUID(as_uuid=True), nullable=True)
    scene_b = Column(UUID(as_uuid=True), nullable=True)
    chapter_b = Column(UUID(as_uuid=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("now()"))
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("now()"))
    issue_id = Column(UUID(as_uuid=True), nullable=True)
    resolved = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "entity", "attribute", "value_a", "value_b"),
        Index("ix_candidates_proj_status", "project_id", "status"),
    )


class ConsistencyTimeCache(Base):
    __tablename__ = "consistency_time_cache"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    raw = Column(String, nullable=False)
    order_seq = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("now()"))

    __table_args__ = (
        PrimaryKeyConstraint("project_id", "raw"),
    )


class FactQueueItem(Base):
    """Persistent write-path task queue (one row per scene)."""

    __tablename__ = "consistency_fact_queue"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    scene_id = Column(UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False)
    content_hash = Column(String(40), nullable=False)
    status = Column(String(12), nullable=False, default="pending", server_default="pending")
    retry_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_retry_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("now()"))
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("now()"))

    __table_args__ = (
        PrimaryKeyConstraint("project_id", "scene_id"),
        Index("ix_consistency_fact_queue_status", "status"),
    )


class ConsistencyReportRecord(Base):
    __tablename__ = "consistency_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by = Column(UUID(as_uuid=True), nullable=True)
    summary = Column(Text, nullable=False, default="")
    stats = Column(JSONB, nullable=False, default=dict)
    meta = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_consistency_reports_project_created", "project_id", "created_at"),
    )


class ConsistencyLog(Base):
    __tablename__ = "consistency_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    report_id = Column(UUID(as_uuid=True), ForeignKey("consistency_reports.id", ondelete="CASCADE"), nullable=True)
    check_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    description = Column(Text, nullable=False)
    suggestion = Column(Text, nullable=True)
    verdict = Column(String(30), nullable=True)
    evidence = Column(JSONB, nullable=True)
    is_resolved = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_consistency_logs_project_resolved", "project_id", "is_resolved"),
    )