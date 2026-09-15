"""add label column to region_borders

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-06
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("region_borders", sa.Column("label", sa.String(100), server_default=""))


def downgrade() -> None:
    op.drop_column("region_borders", "label")
