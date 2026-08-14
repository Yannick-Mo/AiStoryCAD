"""knowledge retrieval indexes; FK/check-constraint hardening; drop dead tables

Fixes from the data-architecture review:

  * knowledge_chunks.embedding        → HNSW index (vector_cosine_ops)
  * knowledge_chunks.content          → GIN expression index
    (to_tsvector('simple', ...) matches the FTS query config)
  * knowledge_chunks(project_id),
    knowledge_chunks(user_id),
    consistency_facts(chapter_id)     → B-tree indexes
  * consistency_facts.entity_vec /
    value_vec, entity_aliases         → dropped (write-only, no readers)
  * conflict_candidates.scene_a/b,
    chapter_a/b                       → FK ON DELETE SET NULL (0015 left them bare)
  * character_relations.trust /
    threat / attraction               → CHECK 0..100 (model-level only until now)
  * scene_fact_cache (0013),
    ai_conversations (0010)           → dropped (ghost tables, no code refs)

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-14
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 3. retrieval indexes (HNSW for cosine search, GIN for FTS, B-tree for filters)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw"
        " ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_content_fts"
        " ON knowledge_chunks USING gin (to_tsvector('simple', content))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_project_id"
        " ON knowledge_chunks (project_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_user_id"
        " ON knowledge_chunks (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_consistency_facts_chapter_id"
        " ON consistency_facts (chapter_id)"
    )

    # 4. drop write-only vector columns and the unused alias table
    op.drop_column("consistency_facts", "entity_vec")
    op.drop_column("consistency_facts", "value_vec")
    op.drop_table("entity_aliases")

    # 10. harden conflict_candidates provenance columns with FKs (SET NULL);
    #     null out any dangling values first so the constraint can be added
    #     on databases that already lost scenes/chapters.
    for col, ref in (
        ("scene_a", "scenes"),
        ("scene_b", "scenes"),
        ("chapter_a", "chapters"),
        ("chapter_b", "chapters"),
    ):
        op.execute(
            f"UPDATE conflict_candidates SET {col} = NULL"
            f" WHERE {col} IS NOT NULL"
            f" AND NOT EXISTS (SELECT 1 FROM {ref} WHERE {ref}.id = conflict_candidates.{col})"
        )
        op.create_foreign_key(
            f"fk_conflict_candidates_{col}",
            "conflict_candidates",
            ref,
            [col],
            ["id"],
            ondelete="SET NULL",
        )

    # 11. character_relations numeric ranges enforced in the database too
    op.execute(
        "UPDATE character_relations SET"
        " trust = LEAST(GREATEST(trust, 0), 100),"
        " threat = LEAST(GREATEST(threat, 0), 100),"
        " attraction = LEAST(GREATEST(attraction, 0), 100)"
    )
    op.create_check_constraint(
        "ck_character_relations_trust", "character_relations", "trust >= 0 AND trust <= 100"
    )
    op.create_check_constraint(
        "ck_character_relations_threat", "character_relations", "threat >= 0 AND threat <= 100"
    )
    op.create_check_constraint(
        "ck_character_relations_attraction",
        "character_relations",
        "attraction >= 0 AND attraction <= 100",
    )

    # 12. ghost tables — no code references remain (IF EXISTS: some databases
    #     were bootstrapped via init_db create_all and never had these)
    op.execute("DROP TABLE IF EXISTS scene_fact_cache")
    op.execute("DROP TABLE IF EXISTS ai_conversations")


def downgrade() -> None:
    # 12. restore ghost tables (simplified original structure)
    op.create_table(
        "ai_conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(200), server_default=""),
        sa.Column("messages", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("token_usage", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "scene_fact_cache",
        sa.Column("scene_id", UUID(as_uuid=True), sa.ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("facts", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scene_fact_cache_project", "scene_fact_cache", ["project_id"])

    # 11. drop the check constraints
    op.drop_constraint("ck_character_relations_attraction", "character_relations", type_="check")
    op.drop_constraint("ck_character_relations_threat", "character_relations", type_="check")
    op.drop_constraint("ck_character_relations_trust", "character_relations", type_="check")

    # 10. drop the candidate provenance FKs
    for col in ("scene_a", "scene_b", "chapter_a", "chapter_b"):
        op.drop_constraint(f"fk_conflict_candidates_{col}", "conflict_candidates", type_="foreignkey")

    # 4. restore vector columns and the alias table
    op.create_table(
        "entity_aliases",
        sa.Column("canonical_entity", sa.Text(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("auto", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("project_id", "alias"),
    )
    from pgvector.sqlalchemy import Vector
    op.add_column("consistency_facts", sa.Column("entity_vec", Vector(1536), nullable=True))
    op.add_column("consistency_facts", sa.Column("value_vec", Vector(1536), nullable=True))

    # 3. drop the retrieval indexes
    op.execute("DROP INDEX IF EXISTS ix_consistency_facts_chapter_id")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_user_id")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_project_id")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_content_fts")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
