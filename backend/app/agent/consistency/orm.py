"""SQLAlchemy ORM models for the consistency engine v2 persistence layer.

Three tables:
  * ``scene_fact_cache``  — per-scene structured facts keyed by content hash,
    so unchanged scenes are never re-extracted on subsequent checks.
  * ``consistency_reports`` — one row per finished check (history).
  * ``consistency_logs``   — issue detail rows, wired to a report and able to
    be marked resolved (the table was created in migration 0010 but never
    read/written; this model finally activates it).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.project.models import Base


class SceneFactCache(Base):
    __tablename__ = "scene_fact_cache"

    scene_id = Column(UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)
    facts = Column(JSONB, nullable=False, default=list)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ConsistencyReportRecord(Base):
    __tablename__ = "consistency_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
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
