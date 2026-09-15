import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import select

from app.database import async_session
from app.project.models import Project
from app.storycad.models import Act, Chapter
from app.storycad.repository import AiStoryCADRepository
from app.storycad.sequence import move_item, ordered_chapter_ids, renumber_container

FAILS = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(label)


async def main():
    async with async_session() as db:
        proj = (await db.execute(select(Project).order_by(Project.created_at))).scalars().first()
        pid = proj.id
        repo = AiStoryCADRepository(db)

        print(f"project={proj.title!r}\n")

        # ── 0. 先清掉**上一次跑**留下的测试数据 ──
        # 验收脚本必须幂等：第二次跑就报警的检查比没有检查更糟，它会教人忽略告警。
        from sqlalchemy import delete as sa_delete
        stale_acts = (await db.execute(
            select(Act.id).where(Act.project_id == pid, Act.name.like("同步测试幕%"))
        )).scalars().all()
        if stale_acts:
            await db.execute(sa_delete(Act).where(Act.id.in_(stale_acts)))   # 连带删掉幕下的章
        stale_chs = (await db.execute(
            select(Chapter.id).where(Chapter.project_id == pid, Chapter.title.like("同步章%"))
        )).scalars().all()
        if stale_chs:
            await db.execute(sa_delete(Chapter).where(Chapter.id.in_(stale_chs)))
        if stale_acts or stale_chs:
            await db.commit()
            await renumber_container(db, Act, pid)
            await db.commit()
            print(f"  （已清理上轮遗留：{len(stale_acts)} 幕 / {len(stale_chs)} 章）\n")

        # ── 1. 同步创建一幕（编辑器路径）──
        r = await repo.sync_editor_data(pid, {"acts": {"created": [{"name": "同步测试幕", "purpose": "验证 editor sync"}]}})
        acts = (await db.execute(select(Act).where(Act.project_id == pid).order_by(Act.sort_order))).scalars().all()
        new_act = next((a for a in acts if a.name == "同步测试幕"), None)
        check("editor sync 建幕成功且接在末尾",
              new_act is not None and new_act.sort_order == len(acts),
              f"幕数={len(acts)} 新幕 sort_order={new_act.sort_order if new_act else None}")

        # ── 2. 同步在该幕下建两章 ──
        await repo.sync_editor_data(pid, {"chapters": {"created": [
            {"act_id": str(new_act.id), "title": "同步章A", "goal": "A 要写什么"},
            {"act_id": str(new_act.id), "title": "同步章B", "goal": "B 要写什么"},
        ]}})
        chs = (await db.execute(
            select(Chapter).where(Chapter.act_id == new_act.id).order_by(Chapter.sort_order)
        )).scalars().all()
        check("editor sync 一次建两章，幕内序号从 1 密集排",
              [c.sort_order for c in chs] == [1, 2] and [c.title for c in chs] == ["同步章A", "同步章B"],
              f"{[(c.sort_order, c.title) for c in chs]}")

        # ── 3. 移动：把 A 插到 B 之后 ──
        a, b = chs[0], chs[1]
        await move_item(db, Chapter, pid, item_id=a.id, after_id=b.id,
                        container_column="act_id", container_id=new_act.id)
        await db.commit()
        chs2 = (await db.execute(
            select(Chapter).where(Chapter.act_id == new_act.id).order_by(Chapter.sort_order)
        )).scalars().all()
        check("move_item(after_id=…) 把 A 插到 B 之后，序号归一化成 1..N",
              [c.title for c in chs2] == ["同步章B", "同步章A"] and [c.sort_order for c in chs2] == [1, 2],
              f"{[(c.sort_order, c.title) for c in chs2]}")

        # ── 4. 换幕：把 A 移到别的幕，两个幕都要重新密集编号 ──
        other_act = next(a_ for a_ in acts if a_.id != new_act.id)
        await move_item(db, Chapter, pid, item_id=a.id, after_id=None, position="end",
                        container_column="act_id", container_id=other_act.id)
        await db.commit()
        left = (await db.execute(
            select(Chapter).where(Chapter.act_id == new_act.id).order_by(Chapter.sort_order)
        )).scalars().all()
        arrived = (await db.execute(
            select(Chapter).where(Chapter.act_id == other_act.id).order_by(Chapter.sort_order)
        )).scalars().all()
        check("换幕成功，且**两个幕**的幕内序号都归一化成连续 1..N",
              [c.sort_order for c in left] == [1]
              and [c.sort_order for c in arrived] == list(range(1, len(arrived) + 1))
              and arrived[-1].id == a.id,
              f"原幕剩 {[(c.sort_order, c.title) for c in left]}，目标幕 {[(c.sort_order, c.title) for c in arrived]}")

        # ── 5. after_id 越界必须报错而不是静默乱插 ──
        try:
            await move_item(db, Chapter, pid, item_id=a.id, after_id=uuid.uuid4(),
                            container_column="act_id", container_id=other_act.id)
            check("after_id 不在目标容器里会报 LookupError", False, "没有报错")
        except LookupError as e:
            check("after_id 不在目标容器里会报 LookupError", True, str(e)[:60])
        await db.rollback()

        # ── 6. 全局章号：跨幕连续，且与幕跨度一致 ──
        ordered = await ordered_chapter_ids(db, pid)
        total = (await db.execute(select(Chapter).where(Chapter.project_id == pid))).scalars().all()
        check("全局章号覆盖全部章节且跨幕连续",
              len(ordered) == len(total),
              f"有序 {len(ordered)} / 实际 {len(total)}")

        # ── 7. 同步删除 + 归一化 ──
        await repo.sync_editor_data(pid, {"chapters": {"deleted": [str(b.id)]}})
        left2 = (await db.execute(
            select(Chapter).where(Chapter.act_id == new_act.id).order_by(Chapter.sort_order)
        )).scalars().all()
        check("删章后幕内序号仍密集（不留空位）",
              [c.sort_order for c in left2] == list(range(1, len(left2) + 1)),
              f"{[(c.sort_order, c.title) for c in left2]}")

        # ── 8. 同步的返回值形状（前端依赖 applied/renumbered）──
        # 注意：上面那次 rollback 会让会话里所有 ORM 对象过期，所以必须**重新查**，
        # 不能复用 proj——读一个已过期对象的属性同样会抛 MissingGreenlet。
        fresh = (await db.execute(select(Project).where(Project.id == pid))).scalar_one()
        r = await repo.sync_editor_data(pid, {"projects": {"updated": [{"global_settings": fresh.global_settings or ""}]}})
        check("editor sync 返回 {applied, renumbered}（不再有 version / timeline_edges）",
              set(r.keys()) == {"applied", "renumbered"},
              str(r))
        check("sync 绝不返回 timeline_edges",
              "timeline_edges" not in r and "version" not in r)

print("=" * 78)
print("编辑器写入路径验证：editor-data/sync + 顺序规范（跑在副本库 storyforge_migtest）")
print("=" * 78)
asyncio.run(main())
print("=" * 78)
print("FAILURES:", len(FAILS), FAILS if FAILS else "")
sys.exit(1 if FAILS else 0)