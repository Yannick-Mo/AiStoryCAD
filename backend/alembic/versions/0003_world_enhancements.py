"""add region borders, color/position fields to world entities

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-06
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("continents", sa.Column("color", sa.String(30), server_default="#6b7280"))
    op.add_column("regions", sa.Column("position_x", sa.Integer, server_default="0"))
    op.add_column("regions", sa.Column("position_y", sa.Integer, server_default="0"))
    op.add_column("factions", sa.Column("color", sa.String(30), server_default="#8b5cf6"))
    op.add_column("factions", sa.Column("position_x", sa.Integer, server_default="0"))
    op.add_column("factions", sa.Column("position_y", sa.Integer, server_default="0"))
    op.create_table(
        "region_borders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_region_id", UUID(as_uuid=True), sa.ForeignKey("regions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_region_id", UUID(as_uuid=True), sa.ForeignKey("regions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("border_type", sa.String(30), server_default=""),
        sa.Column("sort_order", sa.Integer, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("region_borders")
    op.drop_column("factions", "position_y")
    op.drop_column("factions", "position_x")
    op.drop_column("factions", "color")
    op.drop_column("regions", "position_y")
    op.drop_column("regions", "position_x")
    op.drop_column("continents", "color")
