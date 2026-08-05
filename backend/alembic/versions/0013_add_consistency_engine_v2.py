"""add scene_fact_cache, consistency_reports; wire consistency_logs to reports

Implements the persistence layer from 一致性分析引擎v2设计文档 §8:
  * scene_fact_cache   — per-scene structured facts (content-hash keyed)
  * consistency_reports — check history
  * consistency_logs   — new columns report_id / verdict / evidence plus a
    (project_id, is_resolved) index so the resolve workflow is fast

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scene_fact_cache",
        sa.Column("scene_id", UUID(as_uuid=True), sa.ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("facts", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scene_fact_cache_project", "scene_fact_cache", ["project_id"])

    op.create_table(
        "consistency_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("stats", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("meta", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_consistency_reports_project_created", "consistency_reports", ["project_id", "created_at"])

    op.add_column("consistency_logs", sa.Column("report_id", UUID(as_uuid=True), sa.ForeignKey("consistency_reports.id", ondelete="CASCADE"), nullable=True))
    op.add_column("consistency_logs", sa.Column("verdict", sa.String(30), nullable=True))
    op.add_column("consistency_logs", sa.Column("evidence", JSONB, nullable=True))
    op.create_index("ix_consistency_logs_project_resolved", "consistency_logs", ["project_id", "is_resolved"])


def downgrade() -> None:
    op.drop_index("ix_consistency_logs_project_resolved", table_name="consistency_logs")
    op.drop_column("consistency_logs", "evidence")
    op.drop_column("consistency_logs", "verdict")
    op.drop_column("consistency_logs", "report_id")
    op.drop_index("ix_consistency_reports_project_created", table_name="consistency_reports")
    op.drop_table("consistency_reports")
    op.drop_index("ix_scene_fact_cache_project", table_name="scene_fact_cache")
    op.drop_table("scene_fact_cache")
