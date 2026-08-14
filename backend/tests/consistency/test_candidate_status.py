"""Regression tests: conflict-candidate status transitions.

Covers the reviewed bugs:
  * an archived candidate whose value pair reappears must be revived to
    ``pending`` (so it gets judged again), while ``verified`` / ``dismissed``
    human decisions are preserved;
  * a judge batch that fails (LLM exception/timeout) leaves candidates
    ``pending`` instead of marking them ``verified``.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.consistency.checker import ConsistencyChecker
from app.agent.consistency.models import Verdict
from app.agent.consistency.orm import ConflictCandidateRecord, ConsistencyFact
from app.agent.consistency.reconcile import reconcile_project
from app.project.models import Project
from app.storycad.models import Chapter, Scene

pytestmark = pytest.mark.asyncio


async def _project_with_facts(db: AsyncSession, test_user: dict):
    project = Project(title="P", owner_id=test_user["id"])
    db.add(project)
    await db.flush()
    chapter = Chapter(project_id=project.id, title="C")
    db.add(chapter)
    await db.flush()
    s1 = Scene(project_id=project.id, chapter_id=chapter.id, title="S1")
    s2 = Scene(project_id=project.id, chapter_id=chapter.id, title="S2")
    db.add_all([s1, s2])
    await db.flush()
    facts = [
        ConsistencyFact(
            project_id=project.id, scene_id=s1.id, chapter_id=chapter.id,
            entity="阿丽", attribute="瞳色", value="蓝色", value_norm="蓝色",
            evidence="蓝眼睛", source_type="scene_content", is_active=True,
        ),
        ConsistencyFact(
            project_id=project.id, scene_id=s2.id, chapter_id=chapter.id,
            entity="阿丽", attribute="瞳色", value="棕色", value_norm="棕色",
            evidence="棕眼睛", source_type="scene_content", is_active=True,
        ),
    ]
    db.add_all(facts)
    await db.flush()
    return project, facts


async def _candidate(db: AsyncSession, project_id) -> ConflictCandidateRecord | None:
    result = await db.execute(
        select(ConflictCandidateRecord)
        .where(ConflictCandidateRecord.project_id == project_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def test_archived_candidate_revives_to_pending(
    db_session: AsyncSession, test_user: dict
):
    project, facts = await _project_with_facts(db_session, test_user)

    await reconcile_project(db_session, project.id)
    cand = await _candidate(db_session, project.id)
    assert cand is not None
    assert cand.status == "pending"

    # Value pair disappears → archived.
    facts[1].is_active = False
    await db_session.flush()
    await reconcile_project(db_session, project.id)
    cand = await _candidate(db_session, project.id)
    assert cand.status == "archived"

    # Value pair reappears → revived to pending for a fresh judgement.
    facts[1].is_active = True
    await db_session.flush()
    await reconcile_project(db_session, project.id)
    cand = await _candidate(db_session, project.id)
    assert cand.status == "pending"


async def test_human_verdicts_are_not_overridden_by_reconcile(
    db_session: AsyncSession, test_user: dict
):
    """verified/dismissed candidates stay put even while their pair persists."""
    project, _ = await _project_with_facts(db_session, test_user)
    await reconcile_project(db_session, project.id)
    cand = await _candidate(db_session, project.id)
    cand.status = "verified"
    cand.verdict = Verdict.REAL_INCONSISTENCY.value
    await db_session.flush()

    await reconcile_project(db_session, project.id)
    cand = await _candidate(db_session, project.id)
    assert cand.status == "verified"
    assert cand.verdict == Verdict.REAL_INCONSISTENCY.value

    cand.status = "dismissed"
    cand.verdict = Verdict.CHARACTER_DEVELOPMENT.value
    await db_session.flush()
    await reconcile_project(db_session, project.id)
    cand = await _candidate(db_session, project.id)
    assert cand.status == "dismissed"


async def test_judge_failure_keeps_candidates_pending(
    db_session: AsyncSession, test_user: dict
):
    """LLM batch failure → candidates stay pending, counted, re-judged later."""
    project, _ = await _project_with_facts(db_session, test_user)
    await reconcile_project(db_session, project.id)
    cand = await _candidate(db_session, project.id)
    assert cand.status == "pending"

    checker = ConsistencyChecker(db_session, llm_client=MagicMock())
    checker._llm_json = AsyncMock(return_value=None)  # timeout/exception

    issues = await checker._judge_stage(project.id)

    assert issues == []
    assert checker._llm_failures >= 1
    cand = await _candidate(db_session, project.id)
    assert cand.status == "pending"
    assert cand.verdict is None


async def test_judge_success_still_marks_verified(
    db_session: AsyncSession, test_user: dict
):
    """A healthy judge batch still flips candidates to verified (no regression)."""
    project, _ = await _project_with_facts(db_session, test_user)
    await reconcile_project(db_session, project.id)
    cand = await _candidate(db_session, project.id)

    checker = ConsistencyChecker(db_session, llm_client=MagicMock())
    checker._llm_json = AsyncMock(
        return_value={
            "verdicts": [
                {"index": 0, "verdict": "real_inconsistency", "severity": "error",
                 "explanation": "前后矛盾"}
            ]
        }
    )

    issues = await checker._judge_stage(project.id)

    assert len(issues) == 1
    assert issues[0].verdict == Verdict.REAL_INCONSISTENCY
    assert issues[0].candidate_id == str(cand.id)
    cand = await _candidate(db_session, project.id)
    assert cand.status == "verified"
    assert cand.verdict == Verdict.REAL_INCONSISTENCY.value