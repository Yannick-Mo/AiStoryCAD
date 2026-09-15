"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-07-06
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(50), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("title", sa.String(255), server_default="Untitled Project"),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("genre", sa.String(100), server_default=""),
        sa.Column("status", sa.String(20), server_default="init"),
        sa.Column("workflow_stage", sa.String(30), server_default="init"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "project_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("total_words", sa.Integer, server_default="100000"),
        sa.Column("template_type", sa.String(50), server_default="four_act"),
        sa.Column("target_audience", sa.String(100), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "project_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("snapshot", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "acts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False, server_default="新幕"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("color", sa.String(20), server_default="#8b5cf6"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "chapters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("act_id", UUID(as_uuid=True), sa.ForeignKey("acts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False, server_default="新章"),
        sa.Column("goal", sa.Text, server_default=""),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scene_count", sa.Integer, server_default="0"),
        sa.Column("total_words", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "scenes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default="新场"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pov_character", sa.String(100), server_default=""),
        sa.Column("setting", sa.String(200), server_default=""),
        sa.Column("scene_time", sa.String(100), server_default=""),
        sa.Column("summary", sa.Text, server_default=""),
        sa.Column("word_count", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "scene_contents",
        sa.Column("scene_id", UUID(as_uuid=True), sa.ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("content", sa.Text, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "chapter_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("edge_type", sa.String(20), nullable=False, server_default="timeline"),
        sa.Column("label", sa.String(100), server_default=""),
        sa.Column("source_handle", sa.String(20), server_default=""),
        sa.Column("target_handle", sa.String(20), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "characters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(30), server_default="supporting"),
        sa.Column("personality", sa.Text, server_default=""),
        sa.Column("appearance", sa.Text, server_default=""),
        sa.Column("background", sa.Text, server_default=""),
        sa.Column("motivation", sa.Text, server_default=""),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "character_relations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("character_id", UUID(as_uuid=True), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rel_type", sa.String(30), server_default="关联"),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("trust", sa.Integer, server_default="50"),
        sa.Column("threat", sa.Integer, server_default="50"),
        sa.Column("attraction", sa.Integer, server_default="50"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "themes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("color", sa.String(20), server_default="#d4a373"),
        sa.Column("proposition", sa.Text, server_default=""),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "theme_chapters",
        sa.Column("theme_id", UUID(as_uuid=True), sa.ForeignKey("themes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_table(
        "continents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("sort_order", sa.Integer, server_default="0"),
    )
    op.create_table(
        "regions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("continent_id", UUID(as_uuid=True), sa.ForeignKey("continents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("climate", sa.String(100), server_default=""),
        sa.Column("ruler", sa.String(100), server_default=""),
        sa.Column("capital", sa.String(100), server_default=""),
        sa.Column("resources", ARRAY(sa.String), server_default="{}"),
        sa.Column("sort_order", sa.Integer, server_default="0"),
    )
    op.create_table(
        "region_characters",
        sa.Column("region_id", UUID(as_uuid=True), sa.ForeignKey("regions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("character_id", UUID(as_uuid=True), sa.ForeignKey("characters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_table(
        "factions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("leader", sa.String(100), server_default=""),
        sa.Column("goal", sa.Text, server_default=""),
        sa.Column("territory", ARRAY(sa.String), server_default="{}"),
        sa.Column("sort_order", sa.Integer, server_default="0"),
    )
    op.create_table(
        "faction_relations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("factions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), sa.ForeignKey("factions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rel_type", sa.String(30), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("faction_relations")
    op.drop_table("factions")
    op.drop_table("region_characters")
    op.drop_table("regions")
    op.drop_table("continents")
    op.drop_table("theme_chapters")
    op.drop_table("themes")
    op.drop_table("character_relations")
    op.drop_table("characters")
    op.drop_table("chapter_edges")
    op.drop_table("scene_contents")
    op.drop_table("scenes")
    op.drop_table("chapters")
    op.drop_table("acts")
    op.drop_table("project_versions")
    op.drop_table("project_configs")
    op.drop_table("projects")
    op.drop_table("users")
