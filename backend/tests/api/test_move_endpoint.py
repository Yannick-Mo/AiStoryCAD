"""移动端点：``POST /api/projects/{pid}/{entity_type}/{id}/move``

顺序规范（规格第 5 节）：位置只有一份真相（层内序号 + 父子归属），
**移动只能用「插到谁之后 / 插到开头 / 接在末尾」表达，不暴露裸序号**，
同层序号由实现归一化成连续整数。这个端点就是那句话的 HTTP 形态。

端到端打真实路由，顺序一律通过 ``GET /{entity_type}`` 读回验证
（``list_entities`` 按 ``order_by_sequence`` 返回，即按 sort_order）。
"""
import pytest

pytestmark = pytest.mark.asyncio


async def _titles(client, project_id: str, entity: str = "chapters") -> list[str]:
    r = await client.get(f"/api/projects/{project_id}/{entity}")
    assert r.status_code == 200, r.text
    rows = r.json()
    return [row.get("title") or row.get("name") for row in rows]


async def _seed(client, acts: int = 1):
    r = await client.post("/api/projects", json={"title": "Move", "description": ""})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    act_ids = []
    for i in range(acts):
        r = await client.post(f"/api/projects/{pid}/acts",
                              json={"name": f"第{i + 1}幕", "purpose": f"第{i + 1}幕要干什么"})
        assert r.status_code == 200, r.text
        act_ids.append(r.json()["id"])

    chapter_ids = []
    for title in ("A", "B", "C"):
        r = await client.post(f"/api/projects/{pid}/chapters",
                              json={"act_id": act_ids[0], "title": title, "goal": "要写什么"})
        assert r.status_code == 200, r.text
        chapter_ids.append(r.json()["id"])
    return pid, act_ids, chapter_ids


async def test_new_rows_append_and_the_server_owns_the_index(client):
    """裸序号不是客户端的事：建的时候传什么都不算数，服务端接在容器末尾。"""
    pid, act_ids, _ = await _seed(client)
    assert await _titles(client, pid) == ["A", "B", "C"]

    r = await client.post(f"/api/projects/{pid}/chapters",
                          json={"act_id": act_ids[0], "title": "D", "goal": "g", "sort_order": 99})
    assert r.status_code == 200, r.text
    assert await _titles(client, pid) == ["A", "B", "C", "D"]


async def test_move_after_another_chapter(client):
    pid, _, chs = await _seed(client)
    r = await client.post(f"/api/projects/{pid}/chapters/{chs[2]}/move",
                          json={"after_id": chs[0]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert await _titles(client, pid) == ["A", "C", "B"]
    # 序号由实现归一化成连续 1..N
    rows = (await client.get(f"/api/projects/{pid}/chapters")).json()
    assert [row["sort_order"] for row in rows] == [1, 2, 3]


async def test_move_to_start_and_to_end(client):
    pid, _, chs = await _seed(client)
    assert (await client.post(f"/api/projects/{pid}/chapters/{chs[2]}/move",
                              json={"position": "start"})).status_code == 200
    assert await _titles(client, pid) == ["C", "A", "B"]
    assert (await client.post(f"/api/projects/{pid}/chapters/{chs[2]}/move",
                              json={"position": "end"})).status_code == 200
    assert await _titles(client, pid) == ["A", "B", "C"]


async def test_move_across_acts_renumbers_both(client):
    """换幕：两边都要归一化——离开的那一幕不能留空位。"""
    pid, act_ids, chs = await _seed(client)
    r = await client.post(f"/api/projects/{pid}/acts", json={"name": "第二幕", "purpose": "乙"})
    act2 = r.json()["id"]

    r = await client.post(f"/api/projects/{pid}/chapters/{chs[0]}/move",
                          json={"act_id": act2, "position": "end"})
    assert r.status_code == 200, r.text
    assert r.json()["act_id"] == act2

    rows = (await client.get(f"/api/projects/{pid}/chapters")).json()
    by_act: dict[str, list[int]] = {}
    for row in rows:
        by_act.setdefault(row["act_id"], []).append(row["sort_order"])
    assert sorted(by_act[act_ids[0]]) == [1, 2], by_act          # 原幕剩两章，1..2 无空位
    assert by_act[act2] == [1]                                    # 目标幕从 1 开始


async def test_act_move_reorders_acts(client):
    pid, act_ids, _ = await _seed(client, acts=2)
    r = await client.post(f"/api/projects/{pid}/acts/{act_ids[1]}/move",
                          json={"position": "start"})
    assert r.status_code == 200, r.text
    assert await _titles(client, pid, "acts") == ["第2幕", "第1幕"]


async def test_raw_sort_order_is_rejected_by_the_generic_update(client):
    """写路径也拿不到裸序号：PUT 传 sort_order 不会改动位置。"""
    pid, _, chs = await _seed(client)
    r = await client.put(f"/api/projects/{pid}/chapters/{chs[2]}",
                         json={"title": "C", "sort_order": 1})
    assert r.status_code == 200, r.text
    assert await _titles(client, pid) == ["A", "B", "C"], "客户端不该能直接写 sort_order"


async def test_unknown_after_id_is_a_teaching_error(client):
    pid, _, chs = await _seed(client)
    r = await client.post(f"/api/projects/{pid}/chapters/{chs[0]}/move",
                          json={"after_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 400, r.text
    assert "after_id" in r.json()["detail"]


async def test_invalid_position_and_foreign_act_are_rejected(client):
    pid, _, chs = await _seed(client)
    r = await client.post(f"/api/projects/{pid}/chapters/{chs[0]}/move",
                          json={"position": "middle"})
    assert r.status_code == 400

    other = (await client.post("/api/projects", json={"title": "Other", "description": ""})).json()["id"]
    foreign_act = (await client.post(f"/api/projects/{other}/acts",
                                     json={"name": "别人的幕", "purpose": "x"})).json()["id"]
    r = await client.post(f"/api/projects/{pid}/chapters/{chs[0]}/move",
                          json={"act_id": foreign_act})
    assert r.status_code == 400, "跨项目的幕必须被拒绝"


async def test_entity_types_without_a_move_rule_are_rejected(client):
    pid, _, _ = await _seed(client)
    r = await client.post(f"/api/projects/{pid}/characters/00000000-0000-0000-0000-000000000000/move",
                          json={})
    assert r.status_code == 400, r.text
