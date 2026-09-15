"""drop world-building ghost tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-07

Drop tables that have no corresponding ORM models
(continents, regions, region_characters, region_borders, factions, faction_relations)
"""
from collections.abc import Sequence
from alembic import op


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("region_borders")
    op.drop_table("region_characters")
    op.drop_table("regions")
    op.drop_table("continents")
    op.drop_table("faction_relations")
    op.drop_table("factions")


def downgrade() -> None:
    # We do not recreate these tables on downgrade — their models were permanently removed
    pass
