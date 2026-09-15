"""add logline column to projects

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-11
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("logline", sa.Text(), server_default=""))


def downgrade() -> None:
    op.drop_column("projects", "logline")
