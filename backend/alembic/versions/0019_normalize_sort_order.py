"""normalize sort_order into a dense 1..N sequence

``sort_order`` is the single source of truth for order, but historical data
carries duplicate values: the editor numbered chapters per act while the agent
numbered them per project, and AI-created scenes all defaulted to 0.  A
duplicate makes ``ORDER BY sort_order`` non-deterministic, so two loads of the
same project could show different orders.

This migration renumbers every container densely — acts per project, chapters
per act, scenes per chapter, characters per project — ordering rows by their
current sort_order with ``created_at`` / ``id`` as tie-breakers so the relative
order users already see is preserved.  It is idempotent.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ACTS = """
UPDATE acts a SET sort_order = t.rn
FROM (
    SELECT id, row_number() OVER (
        PARTITION BY project_id ORDER BY sort_order, created_at, id
    ) AS rn
    FROM acts
) t
WHERE a.id = t.id AND a.sort_order IS DISTINCT FROM t.rn
"""

_CHAPTERS = """
UPDATE chapters c SET sort_order = t.rn
FROM (
    SELECT id, row_number() OVER (
        PARTITION BY project_id, act_id ORDER BY sort_order, created_at, id
    ) AS rn
    FROM chapters
) t
WHERE c.id = t.id AND c.sort_order IS DISTINCT FROM t.rn
"""

_SCENES = """
UPDATE scenes s SET sort_order = t.rn
FROM (
    SELECT id, row_number() OVER (
        PARTITION BY chapter_id ORDER BY sort_order, created_at, id
    ) AS rn
    FROM scenes
) t
WHERE s.id = t.id AND s.sort_order IS DISTINCT FROM t.rn
"""

_CHARACTERS = """
UPDATE characters ch SET sort_order = t.rn
FROM (
    SELECT id, row_number() OVER (
        PARTITION BY project_id ORDER BY sort_order, created_at, id
    ) AS rn
    FROM characters
) t
WHERE ch.id = t.id AND ch.sort_order IS DISTINCT FROM t.rn
"""


def upgrade() -> None:
    for statement in (_ACTS, _CHAPTERS, _SCENES, _CHARACTERS):
        op.execute(statement)


def downgrade() -> None:
    # Data normalisation: the previous duplicate values carried no information,
    # so there is nothing meaningful to restore.
    pass