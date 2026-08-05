"""Unit tests for phase-0 structural rules."""

from app.agent.consistency.rules import run_structural_rules


class TestStructuralRules:
    def test_duplicate_character_names(self):
        chars = [
            {"id": "c1", "name": "阿丽", "personality": "x", "background": "y", "motivation": "z"},
            {"id": "c2", "name": "阿丽", "personality": "x", "background": "y", "motivation": "z"},
        ]
        issues = run_structural_rules("设定", chars, [], [])
        names = [i.description for i in issues]
        assert any("同名" in d for d in names)

    def test_pov_not_in_characters(self):
        scenes = [{"id": "s1", "chapter_id": "c1", "title": "T", "pov_character": "幽灵"}]
        issues = run_structural_rules("设定", [], [], scenes)
        assert any("POV" in i.description for i in issues)

    def test_missing_character_details_info(self):
        chars = [{"id": "c1", "name": "阿丽"}]
        issues = run_structural_rules("", chars, [], [])
        assert any(i.severity == "info" and "缺少" in i.description for i in issues)

    def test_no_world_settings_info(self):
        issues = run_structural_rules("", [], [], [])
        assert any(i.check_type == "world_rule" and "未设定世界观" in i.description for i in issues)

    def test_duplicate_chapter_sort_order(self):
        chapters = [
            {"id": "ch1", "title": "一", "sort_order": 1},
            {"id": "ch2", "title": "二", "sort_order": 1},
        ]
        issues = run_structural_rules("设定", [], chapters, [])
        assert any("排序值" in i.description and "章节" in i.description for i in issues)

    def test_duplicate_scene_sort_order(self):
        scenes = [
            {"id": "s1", "chapter_id": "c1", "title": "a", "sort_order": 1},
            {"id": "s2", "chapter_id": "c1", "title": "b", "sort_order": 1},
        ]
        issues = run_structural_rules("设定", [], [], scenes)
        assert any("排序值" in i.description and "场景" in i.description for i in issues)

    def test_clean_project_no_issues(self):
        chars = [{"id": "c1", "name": "阿丽", "personality": "p", "background": "b", "motivation": "m"}]
        chapters = [{"id": "ch1", "title": "一", "sort_order": 1}]
        scenes = [{"id": "s1", "chapter_id": "ch1", "title": "a", "sort_order": 1, "pov_character": "阿丽"}]
        issues = run_structural_rules("魔法世界设定", chars, chapters, scenes)
        assert issues == []
