"""add chapter_rhythms

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-07
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chapter_rhythms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("action", sa.Integer, nullable=False, server_default="5"),
        sa.Column("suspense", sa.Integer, nullable=False, server_default="5"),
        sa.Column("emotion", sa.Integer, nullable=False, server_default="5"),
        sa.Column("humor", sa.Integer, nullable=False, server_default="5"),
        sa.Column("intensity", sa.Integer, nullable=False, server_default="5"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("chapter_rhythms")
