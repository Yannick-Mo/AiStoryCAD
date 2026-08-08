"""ORM write-event registration for the consistency v3 ledger (§5.1/5.2).

Kept out of the consistency package so ``storycad`` models never import it.
The handler does exactly one lightweight thing: compute the content hash and
push it into the in-memory inbox. No DB, no transactions, no awaited IO —
the save transaction can never be blocked by the consistency pipeline.
"""
from __future__ import annotations

import logging

from sqlalchemy import event as sa_event

from app.agent.consistency.utils import hash_content
from app.agent.consistency.worker import Inbox
from app.storycad.models import SceneContent

logger = logging.getLogger(__name__)


def register_scene_content_events(inbox: Inbox) -> None:
    """Listen on ``SceneContent`` writes and feed the worker inbox.

    Uses the mapped class directly — the string form is not reliably
    deferred to the mapper registry in SQLAlchemy 2.0.
    """

    @sa_event.listens_for(SceneContent, "after_insert")
    @sa_event.listens_for(SceneContent, "after_update")
    def _on_content_write(mapper, connection, target) -> None:
        try:
            inbox.put(target.project_id, target.scene_id, hash_content(target.content or ""))
        except Exception:
            logger.exception("consistency inbox put failed")

    @sa_event.listens_for(SceneContent, "after_delete")
    def _on_content_delete(mapper, connection, target) -> None:
        try:
            inbox.put(target.project_id, target.scene_id, "")
        except Exception:
            logger.exception("consistency inbox put failed")