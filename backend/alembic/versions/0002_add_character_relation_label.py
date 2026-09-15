"""add label column to character_relations

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-06
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("character_relations", sa.Column("label", sa.String(100), server_default=""))


def downgrade() -> None:
    op.drop_column("character_relations", "label")
