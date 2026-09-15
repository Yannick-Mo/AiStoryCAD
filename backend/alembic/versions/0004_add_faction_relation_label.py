"""add label column to faction_relations

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-06
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("faction_relations", sa.Column("label", sa.String(100), server_default=""))


def downgrade() -> None:
    op.drop_column("faction_relations", "label")
