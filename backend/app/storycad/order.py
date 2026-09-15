"""Deterministic ordering for narrative entities.

``sort_order`` is the single source of truth for the order of **acts and
chapters** everywhere: the outline, the plot canvas, the preview / export and
the agent's own context.  (章是最底层——没有更下层的实体需要排序。）

The column has no UNIQUE constraint, so PostgreSQL is free to return tied rows
in any order and two loads of the same project could disagree.  The
implementation keeps the sequence dense (see ``storycad/sequence.py``), but
every query that orders by ``sort_order`` must still append a deterministic
tie-break — a query that runs mid-transaction can legitimately see a tie.
That is what :func:`order_by_sequence` provides.
"""

from __future__ import annotations

from typing import Any


def tie_break(model: Any, descending: bool = False) -> list[Any]:
    """Stable tie-break columns (``created_at``, primary key) for a model.

    Used on its own for joins that order by several models at once: the
    deepest model's key is enough to make the whole tuple a total order.
    """
    columns = [
        getattr(model, name) for name in ("created_at", "id") if hasattr(model, name)
    ]
    return [c.desc() for c in columns] if descending else columns


def order_by_sequence(model: Any, *extra: Any) -> list[Any]:
    """Return ORDER BY columns for a narrative entity.

    ``sort_order`` first (when the model has one), then :func:`tie_break`, then
    any ``extra`` columns so callers can add e.g. a parent column.
    """
    columns: list[Any] = []
    if hasattr(model, "sort_order"):
        columns.append(model.sort_order)
    columns.extend(tie_break(model))
    columns.extend(extra)
    return columns