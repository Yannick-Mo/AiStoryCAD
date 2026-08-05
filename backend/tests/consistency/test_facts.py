"""Unit tests for fact helpers: chunking, dedup, conflict discovery."""

import pytest

from app.agent.consistency.facts import (
    chunk_text,
    dedup_facts,
    find_conflicts,
    facts_from_extraction,
    scene_meta_facts,
)
from app.agent.consistency.models import Fact, SourceType


class TestChunkText:
    def test_empty(self):
        assert chunk_text("", 10) == []

    def test_short(self):
        assert chunk_text("abc", 10) == ["abc"]

    def test_exact(self):
        assert chunk_text("0123456789", 10) == ["0123456789"]

    def test_split_on_newline(self):
        text = "line1\nline2\nline3"
        blocks = chunk_text(text, 8)
        assert blocks == ["line1", "line2", "line3"]

    def test_hard_slice_when_no_newline(self):
        text = "abcdefghij"
        assert chunk_text(text, 4) == ["abcd", "efgh", "ij"]

    def test_blocks_within_limit(self):
        text = "x" * 100
        for b in chunk_text(text, 30):
            assert len(b) <= 30
        assert "".join(chunk_text(text, 30)) == text


class TestSceneMetaFacts:
    def test_produces_meta_facts(self):
        scene = {"id": "s1", "chapter_id": "c1", "title": "开场", "setting": "森林", "scene_time": "早上", "pov_character": "小明"}
        facts = scene_meta_facts(scene)
        attrs = {f.attribute for f in facts}
        assert attrs == {"所在地", "时间标签", "POV"}
        assert all(f.source_type == SourceType.SCENE_META for f in facts)
        assert all(f.scene_id == "s1" for f in facts)

    def test_skips_empty_fields(self):
        scene = {"id": "s1", "chapter_id": "c1", "title": "开场", "setting": "", "scene_time": "", "pov_character": ""}
        assert scene_meta_facts(scene) == []


class TestDedup:
    def _fact(self, entity, attribute, value, evidence, scene_id):
        return Fact(entity=entity, attribute=attribute, value=value, evidence=evidence, scene_id=scene_id)

    def test_merges_identical_triples(self):
        facts = [
            self._fact("阿丽", "瞳色", "蓝色", "蓝眼睛", "s1"),
            self._fact("阿丽", "瞳色", "蓝色", "眼睛是蓝的", "s2"),
        ]
        merged = dedup_facts(facts)
        assert len(merged) == 1
        assert "蓝眼睛" in merged[0].evidence
        assert "眼睛是蓝的" in merged[0].evidence

    def test_keeps_distinct_values(self):
        facts = [
            self._fact("阿丽", "瞳色", "蓝色", "e1", "s1"),
            self._fact("阿丽", "瞳色", "棕色", "e2", "s2"),
        ]
        assert len(dedup_facts(facts)) == 2

    def test_skips_empty_entity(self):
        facts = [Fact(entity="", attribute="瞳色", value="蓝色", evidence="")]
        assert dedup_facts(facts) == []


class TestFindConflicts:
    def _fact(self, entity, attribute, value, scene_id):
        return Fact(entity=entity, attribute=attribute, value=value, evidence="e", scene_id=scene_id)

    def test_detects_conflicting_values(self):
        facts = [
            self._fact("阿丽", "瞳色", "蓝色", "s1"),
            self._fact("阿丽", "瞳色", "棕色", "s2"),
        ]
        candidates = find_conflicts(facts)
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand.entity == "阿丽"
        assert cand.attribute == "瞳色"
        assert cand.distinct_values == ["蓝色", "棕色"]
        assert len(cand.values) == 2

    def test_no_conflict_for_same_value(self):
        facts = [
            self._fact("阿丽", "瞳色", "蓝色", "s1"),
            self._fact("阿丽", "瞳色", "蓝色", "s2"),
        ]
        assert find_conflicts(facts) == []

    def test_near_miss_is_kept(self):
        """蓝色 vs 深蓝 are distinct values — left for the verify stage."""
        facts = [
            self._fact("阿丽", "瞳色", "蓝色", "s1"),
            self._fact("阿丽", "瞳色", "深蓝", "s2"),
        ]
        assert len(find_conflicts(facts)) == 1

    def test_separate_attributes_no_conflict(self):
        facts = [
            self._fact("阿丽", "瞳色", "蓝色", "s1"),
            self._fact("阿丽", "发色", "黑色", "s2"),
        ]
        assert find_conflicts(facts) == []


class TestFactsFromExtraction:
    def test_parses_valid(self):
        payload = {"facts": [
            {"entity": "阿丽", "attribute": "瞳色", "value": "蓝色", "evidence": "蓝眼睛", "fact_type": "character_state"},
            {"entity": "", "attribute": "x", "value": "y"},  # dropped
        ]}
        facts = facts_from_extraction(payload, "s1", "c1", 0, SourceType.SCENE_CONTENT)
        assert len(facts) == 1
        assert facts[0].scene_id == "s1"
        assert facts[0].source_type == SourceType.SCENE_CONTENT

    def test_invalid_type_defaults(self):
        payload = {"facts": [{"entity": "阿丽", "attribute": "瞳色", "value": "蓝色", "fact_type": "bogus"}]}
        facts = facts_from_extraction(payload, None, None)
        assert len(facts) == 1
        assert facts[0].fact_type == "character_state"

    def test_non_dict_payload(self):
        assert facts_from_extraction([], None, None) == []
        assert facts_from_extraction({"facts": "nope"}, None, None) == []
