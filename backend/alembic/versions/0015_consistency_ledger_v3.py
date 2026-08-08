"""consistency v3 ledger: facts, aliases, candidates, time cache, fact queue

Implements the persistence layer from 一致性分析引擎v3设计文档 §4:
  * consistency_facts          — write-time fact ledger (vectorised)
  * entity_aliases             — alias resolution table
  * conflict_candidates        — judgement records anchored on value pairs
  * consistency_time_cache     — project-level time-expression ordering
  * consistency_fact_queue     — write-path task queue

scene_fact_cache is left in place for now (frozen); a later migration drops
it once the v3 ledger is fully live.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consistency_facts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_id", UUID(as_uuid=True), sa.ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=True),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("entity_vec", Vector(1536), nullable=True),
        sa.Column("attribute", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_norm", sa.Text(), nullable=False),
        sa.Column("value_vec", Vector(1536), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_consistency_facts_proj_active_ent_attr",
        "consistency_facts", ["project_id", "is_active", "entity", "attribute"],
    )
    op.create_index(
        "ix_consistency_facts_scene", "consistency_facts", ["scene_id"],
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "entity_aliases",
        sa.Column("canonical_entity", sa.Text(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("auto", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("project_id", "alias"),
    )

    op.create_table(
        "conflict_candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("attribute", sa.Text(), nullable=False),
        sa.Column("value_a", sa.Text(), nullable=False),
        sa.Column("value_b", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("verdict", sa.String(30), nullable=True),
        sa.Column("severity", sa.String(10), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("evidence_a", sa.Text(), nullable=True),
        sa.Column("evidence_b", sa.Text(), nullable=True),
        sa.Column("scene_a", UUID(as_uuid=True), nullable=True),
        sa.Column("chapter_a", UUID(as_uuid=True), nullable=True),
        sa.Column("scene_b", UUID(as_uuid=True), nullable=True),
        sa.Column("chapter_b", UUID(as_uuid=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("issue_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "entity", "attribute", "value_a", "value_b"),
    )
    op.create_index("ix_candidates_proj_status", "conflict_candidates", ["project_id", "status"])

    op.create_table(
        "consistency_time_cache",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("order_seq", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("project_id", "raw"),
    )

    op.create_table(
        "consistency_fact_queue",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_id", UUID(as_uuid=True), sa.ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_hash", sa.String(40), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("project_id", "scene_id"),
    )
    op.create_index("ix_consistency_fact_queue_status", "consistency_fact_queue", ["status"])


def downgrade() -> None:
    op.drop_index("ix_consistency_fact_queue_status", table_name="consistency_fact_queue")
    op.drop_table("consistency_fact_queue")
    op.drop_table("consistency_time_cache")
    op.drop_index("ix_candidates_proj_status", table_name="conflict_candidates")
    op.drop_table("conflict_candidates")
    op.drop_table("entity_aliases")
    op.drop_index("ix_consistency_facts_scene", table_name="consistency_facts")
    op.drop_index("ix_consistency_facts_proj_active_ent_attr", table_name="consistency_facts")
    op.drop_table("consistency_facts")