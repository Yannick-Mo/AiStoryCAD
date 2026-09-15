import asyncio
import sys

sys.path.insert(0, "/app")

from sqlalchemy import select

from app.database import async_session
from app.agent.tools import get_tool_registry
from app.project.models import Project
from app.storycad.models import Chapter

FAILS = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(label)


async def main():
    reg = get_tool_registry()
    async with async_session() as db:
        proj = (await db.execute(select(Project).order_by(Project.created_at))).scalars().first()
        pid, uid = str(proj.id), str(proj.owner_id)

        # 注意：正文是 deferred 列，脚本里也必须用**列查询**取，不能读 ORM 属性
        rows = (await db.execute(
            select(Chapter.id, Chapter.title, Chapter.content).where(Chapter.project_id == proj.id)
            .order_by(Chapter.sort_order)
        )).all()
        written = next(r for r in rows if (r[2] or ""))
        empty = next(r for r in rows if not (r[2] or ""))
        print(f"已写章 = {written[1]!r}（{len(written[2])} 字符）  空章 = {empty[1]!r}\n")

        async def call(name, **kw):
            res = await reg[name].run(db=db, project_id=pid, user_id=uid, **kw)
            if not res.success:
                print(f"         ! {name}: {res.error}")
            return res

        # ══ 正文写入工具（两个都会先读已有正文，是 deferred 列的高危点）══
        r = await call("continue_chapter", chapter_id=str(written[0]), additional_content="\n\n【续写测试片段】")
        check("continue_chapter 追加成功（读到了已有正文，未抛 MissingGreenlet）",
              r.success and (r.data or {}).get("appended_chars") == len("\n\n【续写测试片段】"),
              f"追加后正文 = {(r.data or {}).get('body_chars')} 字符")

        after = (await db.execute(select(Chapter.content).where(Chapter.id == written[0]))).scalar_one()
        check("追加是**追加**，没有丢掉原文",
              after.startswith(written[2]) and after.endswith("【续写测试片段】"),
              f"原 {len(written[2])} → 现 {len(after)}")

        r = await call("write_chapter", chapter_id=str(written[0]), content="【整体替换测试】")
        check("write_chapter 整体替换成功，并如实报出被替换的字数",
              r.success and (r.data or {}).get("replaced_body_chars") == len(after),
              f"被替换 {r.data.get('replaced_body_chars')} 字符")

        r = await reg["write_chapter"].run(db=db, project_id=pid, user_id=uid,
                                           chapter_id=str(written[0]), content="   ")
        check("write_chapter 拒绝空白正文（不把稿子清空）", not r.success, str(r.error)[:60])

        # 还原
        await call("write_chapter", chapter_id=str(written[0]), content=written[2])

        # ══ 伏笔 ══
        r = await call("create_setup", note="父亲遗像前的香灰里有一枚被磨平的旧硬币",
                       planted_in=str(written[0]), pay_off_in=str(written[0]))
        sid = (r.data or {}).get("setup_id")
        check("create_setup 建伏笔成功", r.success, str(sid))

        r = await call("list_setups")
        one = next((s for s in (r.data or {}).get("setups", []) if s["id"] == sid), None)
        check("list_setups 把章引用解析成全局章号 + 章名 + chapter_id",
              bool(one and one["planted_in"]["global_order"] and one["planted_in"]["title"]
                   and one["planted_in"]["chapter_id"]),
              f"埋在第{one['planted_in']['global_order']}章《{one['planted_in']['title']}》" if one else "未找到")

        r = await call("read_progress")
        overdue = (r.data or {}).get("overdue_setups", [])
        check("伏笔逾期被算出（打算收的章已写、状态仍 open）",
              any(o["id"] == sid for o in overdue), f"逾期 {len(overdue)} 条")

        await call("update_setup", setup_id=sid, status="paid")
        r = await call("read_progress")
        check("改成 paid 后不再算逾期",
              not any(o["id"] == sid for o in (r.data or {}).get("overdue_setups", [])))

        r = await call("create_setup", note="第二章埋下的另一条线",
                       planted_in=str(written[0]), pay_off_in=str(empty[0]))
        sid2 = (r.data or {}).get("setup_id")
        r = await call("read_progress")
        check("收在**还没写**的章上不算逾期",
              not any(o["id"] == sid2 for o in (r.data or {}).get("overdue_setups", [])))

        r = await reg["list_setups"].run(db=db, project_id=pid, user_id=uid, status="whatever")
        check("非法 status 被挡住而不是静默返回空", not r.success, str(r.error)[:45])
        r = await reg["update_setup"].run(db=db, project_id=pid, user_id=uid, setup_id=sid, status="whatever")
        check("update_setup 也挡住非法 status", not r.success, str(r.error)[:45])

        r = await call("search", keyword="旧硬币")
        kinds = {h["kind"] for h in (r.data or {}).get("hits", [])}
        check("search 范围含伏笔", "setup" in kinds, f"命中 {sorted(kinds)}")

        await db.commit()

print("=" * 78)
print("正文写入工具 + 伏笔端到端验证（跑在副本库 storyforge_migtest）")
print("=" * 78)
asyncio.run(main())
print("=" * 78)
print("FAILURES:", len(FAILS), FAILS if FAILS else "")
sys.exit(1 if FAILS else 0)