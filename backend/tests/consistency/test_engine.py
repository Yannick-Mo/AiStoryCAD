"""Unit tests for the v3 consistency check orchestration (fake DB not needed).

The check path under v3 is not a Map-Reduce-Verify pipeline any more —
extraction happens on write (worker) and checks read the ledger. What can be
tested without a database is the checker's pure decision logic: verdict
parsing/fallbacks, candidate→issue conversion, projection parsing and the
report finalisation rules.
"""
import uuid

import pytest

from app.agent.consistency.checker import (
    ConsistencyChecker,
    _candidate_issue_severity,
)
from app.agent.consistency.models import (
    ConsistencyIssue,
    ConsistencyReport,
    Verdict,
)
from app.agent.consistency.orm import ConflictCandidateRecord
from app.agent.consistency.utils import parse_json

CH1 = str(uuid.uuid4())


def _cand(**kw) -> ConflictCandidateRecord:
    base = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "entity": "阿丽",
        "attribute": "瞳色",
        "value_a": "蓝色",
        "value_b": "棕色",
        "evidence_a": "她有着蓝色的眼睛",
        "evidence_b": "她的眼睛是棕色",
        "scene_a": uuid.uuid4(),
        "scene_b": uuid.uuid4(),
        "chapter_a": uuid.uuid4(),
        "chapter_b": uuid.uuid4(),
        "status": "verified",
        "verdict": Verdict.REAL_INCONSISTENCY.value,
        "severity": None,
    }
    base.update(kw)
    return ConflictCandidateRecord(**base)


class TestCandidateToIssueConversion:
    def test_real_inconsistency_becomes_hard_issue(self):
        cand = _cand()
        issues = ConsistencyChecker._candidate_issues(cand)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.verdict == Verdict.REAL_INCONSISTENCY
        assert issue.severity == "error"  # 瞳色 ∈ _HARD_ATTRS → error
        assert issue.entity_type == "character"
        assert "蓝色" in issue.description and "棕色" in issue.description
        assert cand.scene_a in (uuid.UUID(issue.scene_id) if issue.scene_id else None,)

    def test_review_defaults_to_info(self):
        cand = _cand(verdict=Verdict.NEEDS_REVIEW.value)
        issues = ConsistencyChecker._candidate_issues(cand)
        assert len(issues) == 1
        assert issues[0].severity == "info"
        assert issues[0].verdict == Verdict.NEEDS_REVIEW
        assert "人工确认" in issues[0].suggestion

    def test_benign_verdict_yields_no_issue(self):
        cand = _cand(verdict=Verdict.CHARACTER_DEVELOPMENT.value)
        assert ConsistencyChecker._candidate_issues(cand) == []

    def test_severity_override_wins(self):
        cand = _cand(verdict=Verdict.REAL_INCONSISTENCY.value, severity="warning")
        issues = ConsistencyChecker._candidate_issues(cand)
        assert issues[0].severity == "warning"

    def test_issue_carries_candidate_id(self):
        cand = _cand()
        assert ConsistencyChecker._candidate_issues(cand)[0].candidate_id is None  # set by caller
        issue = ConsistencyChecker._candidate_issues(cand)[0]
        issue.candidate_id = str(cand.id)
        assert issue.candidate_id == str(cand.id)


class TestParseJudgeVerdicts:
    def test_all_entries_present(self):
        payload = {"verdicts": [
            {"index": 0, "verdict": "real_inconsistency", "severity": "error", "explanation": "x"},
            {"index": 1, "verdict": "character_development", "severity": "info", "explanation": "y"},
        ]}
        out = ConsistencyChecker._parse_judge_verdicts(None, payload, 2)
        assert [o["verdict"] for o in out] == [Verdict.REAL_INCONSISTENCY, Verdict.CHARACTER_DEVELOPMENT]
        assert out[0]["severity"] == "error"

    def test_missing_entry_degrades_to_needs_review(self):
        out = ConsistencyChecker._parse_judge_verdicts(None, {"verdicts": []}, 3)
        assert all(o["verdict"] == Verdict.NEEDS_REVIEW for o in out)
        assert all(o["severity"] == "info" for o in out)

    def test_bad_verdict_name_degrades(self):
        payload = {"verdicts": [{"index": 0, "verdict": "banana", "severity": "info"}]}
        out = ConsistencyChecker._parse_judge_verdicts(None, payload, 1)
        assert out[0]["verdict"] == Verdict.NEEDS_REVIEW

    def test_non_payload_is_empty(self):
        assert ConsistencyChecker._parse_judge_verdicts(None, None, 1)[0]["verdict"] == Verdict.NEEDS_REVIEW


class TestParseProjection:
    def test_valid_payload(self):
        payload = {"issues": [
            {"severity": "warning", "entity": "魔法学院", "description": "规则前后矛盾",
             "suggestion": "统一", "evidence": "引文"},
        ]}
        issues = ConsistencyChecker._parse_projection_payload(payload)
        assert len(issues) == 1
        assert issues[0].check_type == "global"
        assert issues[0].severity == "warning"
        assert issues[0].entity_type == "魔法学院"

    def test_unknown_severity_normalises_to_info(self):
        payload = {"issues": [{"severity": "spicy", "description": "x"}]}
        issues = ConsistencyChecker._parse_projection_payload(payload)
        assert issues[0].severity == "info"

    def test_empty_or_junk(self):
        assert ConsistencyChecker._parse_projection_payload(None) == []
        assert ConsistencyChecker._parse_projection_payload({"issues": "nope"}) == []
        assert ConsistencyChecker._parse_projection_payload({"issues": [{"description": ""}]}) == []


class TestSeverityHeuristic:
    def test_hard_attribute_is_error(self):
        assert _candidate_issue_severity("瞳色") == "error"
        assert _candidate_issue_severity("发色") == "error"

    def test_world_rule_is_error(self):
        assert _candidate_issue_severity("规则") == "error"

    def test_soft_attribute_is_warning(self):
        assert _candidate_issue_severity("习惯") == "warning"


class TestReportFinalize:
    def test_clean_summary(self):
        report = ConsistencyReport(project_id=CH1, issues=[])
        report.finalize(CH1)
        assert report.summary == "未发现一致性问题"
        assert report.stats == {"errors": 0, "warnings": 0, "infos": 0}

    def test_counts_severities(self):
        issues = [
            ConsistencyIssue(check_type="character", severity="error", entity_type="character", description="a"),
            ConsistencyIssue(check_type="character", severity="warning", entity_type="character", description="b"),
            ConsistencyIssue(check_type="character", severity="info", entity_type="character", description="c"),
        ]
        report = ConsistencyReport(project_id=CH1, issues=issues)
        report.finalize(CH1)
        assert "1 个错误" in report.summary and "1 个警告" in report.summary


class TestParseJson:
    def test_raw_json(self):
        assert parse_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_text_before_json(self):
        assert parse_json('结果如下：{"a": 1}') == {"a": 1}

    def test_invalid(self):
        assert parse_json("not json") is None
        assert parse_json("") is None