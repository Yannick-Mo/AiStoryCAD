"""local-tool: model_config table; drop users table and its FK references

Local single-user tool conversion (feat/local-tool):

  * new ``model_config`` table — single-row (id=1) LLM/embedding provider
    settings editable from the frontend settings modal
  * ``users`` table and the JWT/bcrypt auth stack are removed; the
    owner/user columns on projects / conversations / knowledge_chunks /
    consistency_reports are kept as plain (nullable) UUIDs with their FK
    constraints dropped so all existing queries keep working.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. single-row model provider configuration
    op.create_table(
        "model_config",
        sa.Column("id", sa.Integer(), primary_key=True, server_default=sa.text("1")),
        sa.Column("main_model", sa.String(100), nullable=False, server_default="deepseek-v4-flash"),
        sa.Column("main_base_url", sa.String(500), nullable=False, server_default="https://api.deepseek.com/v1"),
        sa.Column("main_api_key", sa.String(500), nullable=False, server_default=""),
        sa.Column("middle_model", sa.String(100), nullable=True),
        sa.Column("fallback_models", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("embedding_base_url", sa.String(500), nullable=True),
        sa.Column("embedding_model", sa.String(100), nullable=True, server_default="text-embedding-3-small"),
        sa.Column("embedding_api_key", sa.String(500), nullable=True),
        sa.Column("embedding_proxy", sa.String(500), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_check_constraint(
        "ck_model_config_single_row", "model_config", "id = 1"
    )

    # 2. drop FK constraints referencing users (default Postgres names)
    op.drop_constraint("projects_owner_id_fkey", "projects", type_="foreignkey")
    op.drop_constraint("conversations_user_id_fkey", "conversations", type_="foreignkey")
    op.drop_constraint("knowledge_chunks_user_id_fkey", "knowledge_chunks", type_="foreignkey")
    op.drop_constraint("consistency_reports_requested_by_fkey", "consistency_reports", type_="foreignkey")

    # 3. drop the users table itself
    op.drop_table("users")


def downgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_foreign_key(
        "projects_owner_id_fkey", "projects", "users", ["owner_id"], ["id"]
    )
    op.create_foreign_key(
        "conversations_user_id_fkey", "conversations", "users", ["user_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "knowledge_chunks_user_id_fkey", "knowledge_chunks", "users", ["user_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "consistency_reports_requested_by_fkey", "consistency_reports", "users", ["requested_by"], ["id"]
    )

    op.drop_constraint("ck_model_config_single_row", "model_config", type_="check")
    op.drop_table("model_config")
