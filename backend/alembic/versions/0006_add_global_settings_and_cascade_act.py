"""add global_settings to projects, cascade act delete

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-06
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("global_settings", sa.Text, server_default=""))
    op.drop_constraint("chapters_act_id_fkey", "chapters", type_="foreignkey")
    op.create_foreign_key(
        "chapters_act_id_fkey", "chapters", "acts",
        ["act_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_column("projects", "global_settings")
    op.drop_constraint("chapters_act_id_fkey", "chapters", type_="foreignkey")
    op.create_foreign_key(
        "chapters_act_id_fkey", "chapters", "acts",
        ["act_id"], ["id"], ondelete="SET NULL"
    )
