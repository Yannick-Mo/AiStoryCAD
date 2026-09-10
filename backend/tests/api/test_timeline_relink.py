"""情节幕布顶部栏的「重连时序」按钮。

顺序的唯一真相是 ``sort_order``；这个接口按需把它重新投影成时序连线，
用户刚调完章节顺序时不必等下一次同步、也不用切视图就能让连线跟上。
"""
import uuid

from httpx import AsyncClient


async def _project_with_chapters(client: AsyncClient, count: int = 3):
    project = (await client.post("/api/projects", json={"title": "Relink"})).json()
    pid = project["id"]
    act_id = str(uuid.uuid4())
    chapters = [
        {"id": str(uuid.uuid4()), "act_id": act_id, "title": f"第 {i + 1} 章"}
        for i in range(count)
    ]
    resp = await client.post(f"/api/projects/{pid}/editor-data/sync", json={
        "acts": {"created": [{"id": act_id, "name": "第一幕", "sort_order": 1}]},
        "chapters": {"created": chapters},
    })
    assert resp.status_code == 200, resp.text
    return pid, act_id, [c["id"] for c in chapters]


class TestRelinkTimelineEndpoint:
    async def test_relink_returns_the_chain_in_reading_order(self, client):
        pid, _act, ids = await _project_with_chapters(client)
        resp = await client.post(f"/api/projects/{pid}/timeline/relink")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert [(e["source_id"], e["target_id"]) for e in body["timeline_edges"]] == list(
            zip(ids, ids[1:])
        )

    async def test_relink_replaces_a_stray_timeline_edge(self, client):
        pid, _act, ids = await _project_with_chapters(client, count=2)
        # 手画的、与顺序相反的时序边：只在边的同步里不会被清掉
        await client.post(f"/api/projects/{pid}/editor-data/sync", json={
            "edges": {"created": [
                {"id": str(uuid.uuid4()), "source_id": ids[1], "target_id": ids[0],
                 "edge_type": "timeline"},
            ]},
        })
        before = (await client.get(f"/api/projects/{pid}/editor-data")).json()
        pairs_before = {
            (e["source_id"], e["target_id"]) for e in before["edges"]
            if e["edge_type"] == "timeline"
        }
        assert (ids[1], ids[0]) in pairs_before

        body = (await client.post(f"/api/projects/{pid}/timeline/relink")).json()
        assert body["deleted"] == 1
        assert {(e["source_id"], e["target_id"]) for e in body["timeline_edges"]} == {
            (ids[0], ids[1])
        }

    async def test_relink_keeps_matching_edges_so_it_is_idempotent(self, client):
        pid, _act, _ids = await _project_with_chapters(client)
        first = (await client.post(f"/api/projects/{pid}/timeline/relink")).json()
        second = (await client.post(f"/api/projects/{pid}/timeline/relink")).json()
        assert (second["created"], second["deleted"]) == (0, 0)
        assert {e["id"] for e in first["timeline_edges"]} == {
            e["id"] for e in second["timeline_edges"]
        }

    async def test_relink_of_a_foreign_project_is_404(self, client):
        resp = await client.post(f"/api/projects/{uuid.uuid4()}/timeline/relink")
        assert resp.status_code == 404