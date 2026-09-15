"""add note column to themes table

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-07
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("themes", sa.Column("note", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("themes", "note")
