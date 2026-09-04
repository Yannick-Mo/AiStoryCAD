"""Semantics of result truncation + pagination clamps (batch B fixes):
1. Long payload fields (content/... ) keep head AND tail, never collapse to a
   200-char digest.
2. read_chapter's scenes array keeps ALL entries (ID-list semantics), so a
   chapter with 30 scenes no longer silently returns 3.
3. Body/settings reads return the FULL text by default (single-result ceiling
   ~100k chars); pagination only kicks in beyond that — no "200 chars but says
   finished".
4. read_scene written flag follows word_count (single definition).
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.tools import streaming_executor as se
from app.agent.tools.project_tools import (
    ReadGlobalSettingsTool, ReadSceneContentTool, ReadSceneTool,
)


class _Q:
    def __init__(self, rows=None, scalars=None):
        self._rows = rows if rows is not None else []
        self._scalars = scalars if scalars is not None else []

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None


class TestLongTextHeadTail:
    def test_content_field_keeps_head_and_tail(self):
        body = "序章开头" + "中间内容" * 3000 + "大结局"
        payload = json.dumps({"scene_id": str(uuid.uuid4()), "content": body,
                              "meta": "x" * 600})
        out = se._smart_summarise(payload, 8000, "read_scene_content")
        assert out.startswith("{")
        assert "序章开头" in out
        assert "大结局" in out
        assert "省略" in out
        assert "全文共" in out
        collapsed = json.loads(out)
        assert collapsed["meta"].startswith("x" * 200)
        assert collapsed["content"].endswith("大结局")

    def test_read_chapter_scenes_array_keeps_all_entries(self):
        rows = [{"id": str(uuid.uuid4()), "title": f"场景{i}",
                 "summary": "蓝图内容" * 300} for i in range(30)]
        payload = json.dumps({"chapter_id": str(uuid.uuid4()), "goal": "目标" * 2000,
                              "scenes": rows}, ensure_ascii=False)
        out = se._smart_summarise(payload, 16000, "read_chapter")
        parsed = json.loads(out)
        assert len(parsed["scenes"]) == 30
        assert parsed["scenes"][0]["summary"].endswith("chars]")
        assert "场景29" in parsed["scenes"][29]["title"]

    def test_non_long_text_key_still_200_digest(self):
        payload = json.dumps({"note": "x" * 3000})
        out = se._smart_summarise(payload, 1000, "some_tool")
        parsed = json.loads(out)
        assert len(parsed["note"]) < 300
        assert "[3000 chars]" in parsed["note"]


def _db_with(project_row, scene_row=None, content_row=None):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),  # verify owner
        _Q(scalars=[project_row]),
        _Q(scalars=[scene_row]),
        _Q(scalars=[content_row]),
    ])
    return db


class TestSinglePageClamp:
    async def test_scene_content_default_reads_whole_body(self):
        pid = uuid.uuid4()
        sc_id = uuid.uuid4()
        body = "字" * 30000
        scene = SimpleNamespace(id=sc_id, project_id=pid, title="场",
                                word_count=0)
        content = SimpleNamespace(scene_id=sc_id, content=body)
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[scene]),                                  # select Scene
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),       # verify owner
            _Q(scalars=[content]),                                # select SceneContent
        ])
        res = await ReadSceneContentTool().run(
            db, project_id=str(pid), user_id="u", scene_id=str(sc_id))
        assert res.success
        assert res.data["content"] == body       # full 30k, no pagination tax
        assert res.data["content_has_more"] is False
        assert res.data["body_chars"] == 30000

    async def test_scene_content_offsets_still_work(self):
        sc_id = uuid.uuid4()
        pid = uuid.uuid4()
        scene = SimpleNamespace(id=sc_id, project_id=pid, title="场", word_count=5)
        content = SimpleNamespace(scene_id=sc_id, content="abcdefgh")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[scene]),                                  # select Scene
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),       # verify owner
            _Q(scalars=[content]),                                # select SceneContent
        ])
        res = await ReadSceneContentTool().run(
            db, project_id=str(pid), user_id="u", scene_id=str(sc_id),
            content_offset=3, content_limit=4)
        assert res.data["content"] == "defg"
        assert res.data["content_has_more"] is True

    async def test_absurdly_long_body_clamped_with_true_has_more(self):
        # Beyond the ~100k single-result ceiling pagination takes over — the
        # has_more flag must stay truthful (never "200 chars but finished").
        pid = uuid.uuid4()
        sc_id = uuid.uuid4()
        body = "字" * 300000
        scene = SimpleNamespace(id=sc_id, project_id=pid, title="场", word_count=0)
        content = SimpleNamespace(scene_id=sc_id, content=body)
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[scene]),
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),
            _Q(scalars=[content]),
        ])
        res = await ReadSceneContentTool().run(
            db, project_id=str(pid), user_id="u", scene_id=str(sc_id))
        assert res.success
        assert len(res.data["content"]) <= 99900
        assert res.data["content_has_more"] is True

    async def test_global_settings_default_reads_whole(self):
        pid = uuid.uuid4()
        proj = SimpleNamespace(id=pid, title="T", global_settings="设" * 25000)
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),  # verify
            _Q(scalars=[proj]),
        ])
        res = await ReadGlobalSettingsTool().run(
            db, project_id=str(pid), user_id="u")
        assert res.success
        assert res.data["content"] == "设" * 25000
        assert res.data["content_has_more"] is False


class TestWrittenUsesWordCount:
    async def test_written_true_when_word_count_gt_zero(self):
        sc_id = uuid.uuid4()
        pid = uuid.uuid4()
        scene = SimpleNamespace(id=sc_id, project_id=pid, title="场",
                                word_count=120, summary="蓝图", pov_character="",
                                setting="", scene_time="")
        content = SimpleNamespace(scene_id=sc_id, content="正文内容")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _Q(scalars=[scene]),                                  # select Scene
            _Q(scalars=[SimpleNamespace(id=uuid.uuid4())]),       # verify owner
            _Q(scalars=[content]),                                # select SceneContent
        ])
        with __import__("unittest.mock").mock.patch(
                "app.agent.tools.project_tools.row_to_dict",
                return_value={"id": str(sc_id), "word_count": 120}):
            res = await ReadSceneTool().run(
                db, project_id=str(pid), user_id="u", scene_id=str(sc_id))
        assert res.success
        assert res.data["written"] is True
