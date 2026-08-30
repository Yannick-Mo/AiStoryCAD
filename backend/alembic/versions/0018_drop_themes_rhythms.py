"""drop themes / theme_chapters / chapter_rhythms

Remove the theme and rhythm feature entirely (frontend views, backend
logic and data). Tables dropped in dependency order.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("theme_chapters")
    op.drop_table("chapter_rhythms")
    op.drop_table("themes")


def downgrade() -> None:
    pass
