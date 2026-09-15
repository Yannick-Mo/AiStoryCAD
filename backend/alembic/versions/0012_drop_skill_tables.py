"""drop skill_definitions and project_skills tables — skills are now file-driven

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-13
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("project_skills")
    op.drop_table("skill_definitions")


def downgrade() -> None:
    op.create_table(
        "skill_definitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("genre", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "project_skills",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("skill_name", sa.String(100), sa.ForeignKey("skill_definitions.name"), primary_key=True),
        sa.Column("config_override", JSONB, server_default="{}"),
        sa.Column("sort_order", sa.Integer, server_default="0"),
    )
