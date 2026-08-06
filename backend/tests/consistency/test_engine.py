"""Integration tests for the v2 consistency pipeline (fake LLM).

These exercise the full Map-Reduce-Verify pipeline end to end without a real
LLM or database — the session is an ``AsyncMock`` returning canned rows and
the LLM is a scripted generator.
"""
import asyncio
import hashlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.consistency.engine import ConsistencyPipeline, _parse_json
from app.agent.consistency.models import Verdict

CH1 = str(uuid.uuid4())
S1 = str(uuid.uuid4())
S2 = str(uuid.uuid4())
C1 = str(uuid.uuid4())

# Long enough to pass the `consistency_skip_small_scene_chars` (500) gate.
FILLER = "她环顾四周，月光透过高窗洒在石板地上，远处传来低沉的钟声，空气中弥漫着旧书与烛蜡的气味。"
CONTENT1 = "她的眼睛是蓝色的。" + FILLER * 12
CONTENT2 = "她的眼睛是棕色的。" + FILLER * 12


class FakeLLM:
    """Scripted chat_stream_tokens that answers by prompt content."""

    def __init__(self, mode="normal"):
        self.mode = mode
        self.calls: list[dict] = []

    def fork(self):
        return self

    async def chat_stream_tokens(self, messages, **kw):
        if self.mode == "error":
            raise RuntimeError("Insufficient Balance")
        user = next(m.content for m in messages if m.role == "user")
        self.calls.append({
            "max_tokens": kw.get("max_tokens"),
            "reasoning_effort": kw.get("reasoning_effort"),
            "temperature": kw.get("temperature"),
            "user": user,
        })
        yield self._respond(user)

    def _respond(self, user: str) -> str:
        if self.mode == "garbage":
            return "this is definitely not json"
        if self.mode == "verify_garbage":
            # extraction succeeds, verify fails to parse
            if "<candidates>" in user or "<timeline_data>" in user:
                return "this is definitely not json"
        if "<candidates>" in user:
            if self.mode == "normal":
                return json.dumps({"verdicts": [
                    {"candidate_index": 0, "verdict": "real_inconsistency", "explanation": "瞳色矛盾", "severity": "error"}
                ]})
            return json.dumps({"verdicts": []})
        if "<timeline_data>" in user:
            return json.dumps({"issues": [
                {"check_type": "global", "severity": "warning", "entity_type": "chapter",
                 "entity_id": CH1, "description": "跨章时间线矛盾", "suggestion": "检查", "chapter_id": CH1}
            ]})
        if "章节标题=角色设定" in user:
            return json.dumps({"facts": [{"entity": "阿丽", "attribute": "瞳色", "value": "蓝色", "evidence": "设定"}]})
        if "章节标题=世界观设定" in user:
            return json.dumps({"facts": [{"entity": "世界", "attribute": "规则", "value": "魔法", "evidence": "设定"}]})
        if "scene_content>" in user:
            value = "蓝色" if "蓝色" in user else "棕色"
            return json.dumps({"facts": [{"entity": "阿丽", "attribute": "瞳色", "value": value, "evidence": "原文"}]})
        return "{}"


def _scalar_row(obj):
    m = MagicMock()
    m.scalar_one_or_none.return_value = obj
    return m


def _rows_result(objs):
    m = MagicMock()
    m.scalars.return_value.all.return_value = objs
    return m


def _make_db(cache_rows=None):
    """Build an AsyncMock session returning the full load sequence."""
    project = SimpleNamespace(id=CH1, global_settings="魔法世界")
    characters = [{"id": C1, "name": "阿丽", "sort_order": 0,
                   "personality": "", "appearance": "眼睛是蓝色的", "background": "", "motivation": ""}]
    chapters = [{"id": CH1, "title": "第一章", "sort_order": 1, "act_id": None, "status": "draft", "goal": ""}]
    scenes = [
        {"id": S1, "chapter_id": CH1, "title": "开场", "sort_order": 1, "pov_character": "阿丽",
         "setting": "", "scene_time": "", "summary": ""},
        {"id": S2, "chapter_id": CH1, "title": "中段", "sort_order": 2, "pov_character": "阿丽",
         "setting": "", "scene_time": "", "summary": ""},
    ]
    contents = [
        SimpleNamespace(scene_id=uuid.UUID(S1), content=CONTENT1),
        SimpleNamespace(scene_id=uuid.UUID(S2), content=CONTENT2),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_row(project),
        _rows_result(characters),
        _rows_result(chapters),
        _rows_result(scenes),
        _rows_result([]),            # acts
        _rows_result([]),            # chapter edges
        _rows_result(contents),      # scene contents
        _rows_result(cache_rows or []),  # scene_fact_cache
    ])
    db.get = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


def _cache_row(scene_id: str, content: str):
    return SimpleNamespace(
        scene_id=uuid.UUID(scene_id),
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        facts=[{"entity": "阿丽", "attribute": "瞳色", "value": "蓝色", "evidence": "原文",
                "fact_type": "character_state", "source_type": "scene_content",
                "scene_id": scene_id, "chapter_id": CH1, "block_index": 0}],
        error=None,
    )


async def _run(db, llm) -> dict:
    pipeline = ConsistencyPipeline(db, llm)
    report = await pipeline.run(str(uuid.UUID(S1)).replace(str(uuid.UUID(S1)), "deadbeef") if False else CH1)
    return report.model_dump(mode="json")


def _scene_extraction_calls(llm) -> list[dict]:
    """Calls targeting scene *content* blocks (not profile/settings baselines)."""
    return [
        c for c in llm.calls
        if "scene_content>" in c["user"]
        and "章节标题=角色设定" not in c["user"]
        and "章节标题=世界观设定" not in c["user"]
    ]


class TestPipelineEndToEnd:
    async def test_full_pipeline_detects_conflict(self):
        db = _make_db()
        llm = FakeLLM()
        report = await _run(db, llm)
        severities = [i["severity"] for i in report["issues"]]
        # verify stage emitted a hard error for the eye-colour conflict.
        assert "error" in severities
        verify = [i for i in report["issues"] if i["severity"] == "error"]
        assert verify and verify[0]["entity_type"] == "character"
        assert verify[0]["verdict"] == Verdict.REAL_INCONSISTENCY.value
        # global stage issue present.
        assert any(i["check_type"] == "global" for i in report["issues"])
        assert report["summary"] != ""

    async def test_all_llm_calls_respect_max_tokens_gate(self):
        db = _make_db()
        llm = FakeLLM()
        await _run(db, llm)
        assert llm.calls, "expected LLM calls"
        for call in llm.calls:
            assert call["max_tokens"] <= 8192, f"max_tokens {call['max_tokens']} exceeds hard gate"

    async def test_cache_hit_skips_extraction(self):
        # Pre-seeded cache with matching hashes → no scene_content extraction.
        db = _make_db(cache_rows=[_cache_row(S1, CONTENT1), _cache_row(S2, CONTENT2)])
        llm = FakeLLM()
        await _run(db, llm)
        extraction_calls = _scene_extraction_calls(llm)
        assert extraction_calls == []

    async def test_incremental_re_extracts_only_changed_scene(self):
        # S1 cached with a STALE hash → re-extracted; S2 cached fresh → skipped.
        stale = _cache_row(S1, CONTENT1)
        stale.content_hash = hashlib.sha256(b"different").hexdigest()
        db = _make_db(cache_rows=[stale, _cache_row(S2, CONTENT2)])
        llm = FakeLLM()
        await _run(db, llm)
        extraction_calls = _scene_extraction_calls(llm)
        assert len(extraction_calls) == 1
        assert "蓝色" in extraction_calls[0]["user"] or CONTENT1 in extraction_calls[0]["user"]

    async def test_empty_llm_results_do_not_crash(self):
        """Empty LLM output is NOT a failure — rules still run and report builds."""
        db = _make_db()
        llm = FakeLLM(mode="garbage")
        report = await _run(db, llm)
        # No exception, structural rules still present, and the verify stage
        # conservatively marks candidates as needs_review.
        assert any(i["check_type"] in ("character", "world_rule") for i in report["issues"])
        assert report["summary"] != ""

    async def test_verify_failure_defaults_to_needs_review(self):
        db = _make_db()
        llm = FakeLLM(mode="verify_garbage")
        report = await _run(db, llm)
        needs_review = [i for i in report["issues"] if i.get("verdict") == Verdict.NEEDS_REVIEW.value]
        assert needs_review, "garbage verify output must degrade to needs_review info issues"
        assert all(i["severity"] == "info" for i in needs_review)

    async def test_llm_failures_are_reported_not_swallowed(self):
        """LLM exceptions/timeouts must not vanish into a bogus 'no issues' report."""
        db = _make_db()
        llm = FakeLLM(mode="error")
        report = await _run(db, llm)
        assert report["meta"]["llm_failures"] > 0
        assert report["meta"]["llm_failure_sample"]
        assert "LLM 调用失败" in report["summary"]


class TestParseJson:
    def test_raw_json(self):
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_text_before_json(self):
        assert _parse_json('结果如下：{"a": 1}') == {"a": 1}

    def test_invalid(self):
        assert _parse_json("not json") is None
        assert _parse_json("") is None


class TestConsistencyPipelineConstruction:
    async def test_progress_cb_receives_stages(self):
        db = _make_db()
        llm = FakeLLM()
        stages: list[str] = []
        async def cb(stage, done, total, message):
            stages.append(stage)
        pipeline = ConsistencyPipeline(db, llm, progress_cb=cb)
        await pipeline.run(CH1)
        assert "extract" in stages
        assert "merge" in stages
        assert "verify" in stages
        assert "global" in stages
