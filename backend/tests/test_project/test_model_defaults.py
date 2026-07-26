import asyncio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.project.models import Base, Project
from app.project.repository import ProjectRepository


@pytest.mark.asyncio
async def test_created_at_differs_between_rows(db_session: AsyncSession, test_user: dict):
    """Two projects created at different times must not share created_at.

    If Column(default=datetime.now(...)) is evaluated at import time, every row
    gets the same frozen timestamp and updated_at ordering breaks.
    """
    repo = ProjectRepository(db_session)
    first = await repo.create("First", "", test_user["id"])
    await asyncio.sleep(0.05)
    second = await repo.create("Second", "", test_user["id"])
    assert first.created_at != second.created_at


def test_project_created_at_default_is_callable():
    """Direct check on one column: default.arg must be a callable, not a frozen datetime."""
    assert callable(Project.__table__.c.created_at.default.arg)


def test_all_timestamp_column_defaults_are_callable():
    """Every created_at/updated_at column on every registered model must use a
    callable default/onupdate, so timestamps are computed per-row at insert/update
    time instead of once at module import."""
    # Import all model modules so their mappers register on Base.
    import app.project.models  # noqa: F401
    import app.storycad.models  # noqa: F401
    import app.knowledge.models  # noqa: F401
    import app.user.models  # noqa: F401

    offenders = []
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        for col in table.columns:
            if col.name not in ("created_at", "updated_at"):
                continue
            if col.default is not None and not callable(col.default.arg):
                offenders.append(f"{table.name}.{col.name} default={col.default.arg!r}")
            if col.onupdate is not None and not callable(col.onupdate.arg):
                offenders.append(f"{table.name}.{col.name} onupdate={col.onupdate.arg!r}")
    assert not offenders, "Frozen (import-time) timestamp defaults found:\n" + "\n".join(offenders)
