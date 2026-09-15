import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import select

from app.database import async_session
from app.agent.tools import get_tool_registry
from app.project.models import Project
from app.storycad.models import Chapter

FAILS: list[str] = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(label)


async def main():
    reg = get_tool_registry()
    async with async_session() as db:
        proj = (await db.execute(select(Project).order_by(Project.created_at))).scalars().first()
        pid = str(proj.id)
        uid = str(proj.owner_id)

        async def call(name, **kw):
            res = await reg[name].run(db=db, project_id=pid, user_id=uid, **kw)
            if not res.success:
                print(f"         ! {name} 失败: {res.error}")
            return res

        print(f"project: {proj.title!r}  id={pid}\n")

        # ── 1. 这本书是什么、要走到哪、主线怎么走 ──
        r = await call("read_project")
        d = r.data or {}
        check("Q1 作品层答得出：书名/类型/梗概/终局/主线骨架/全局设定/目标字数",
              r.success and all(k in d for k in ("title", "genre", "logline", "ending", "spine",
                                                 "global_settings", "total_words")),
              f"title={d.get('title')!r} ending={d.get('ending_chars')}字 spine={d.get('spine')!r}")

        # ── 2. 第 N 幕承担什么转变、它有几章、起止全局章号 ──
        r = await call("read_acts")
        acts = (r.data or {}).get("acts", [])
        check("Q2 幕层答得出：要干什么 + 章数 + 起止全局章号",
              r.success and all(
                  k in a for a in acts
                  for k in ("purpose", "chapter_count", "first_chapter_order", "last_chapter_order")
              ),
              f"{len(acts)} 幕: " + "; ".join(
                  f"{a['name']}({a['chapter_count']}章 第{a['first_chapter_order']}-{a['last_chapter_order']})"
                  for a in acts))

        # ── 3. 第 N 章：要写什么/写成了什么/哪一幕/幕内第几/全局第几/写完没有 ──
        r = await call("read_chapters", chapter_from=1, chapter_to=5)
        w = r.data or {}
        chs = w.get("chapters", [])
        check("Q3 章层答得出：要写什么 + 写成了什么 + 状态 + 所属幕 + 章序 + 全局章号 + 是否已写",
              r.success and chs and all(
                  k in c for c in chs
                  for k in ("goal", "result", "status", "act_name", "sort_order",
                            "global_order", "body_chars")
              ),
              f"共 {w['total_chapters']} 章，返回 {len(chs)} 章，truncated={w['truncated']}")
        for c in chs[:3]:
            print(f"        第{c['global_order']}章《{c['title']}》 {c['status']} "
                  f"正文{c['body_chars']}字符 幕={c['act_name']}")

        # ── 4. 第 N-1 章成了什么样（这一章的起点） ──
        target = next((c for c in chs if c["global_order"] >= 2), chs[0])
        r = await call("read_chapter_package", chapter_id=target["id"])
        pkg = r.data or {}
        prev = pkg.get("prev_chapter")
        check("Q4 范围读答得出：上一章写成了什么（这一章的起点）",
              r.success and (prev is None or "result" in prev),
              f"第{target['global_order']}章的上一章 = {prev['title'] if prev else None!r}")

        # ── 5. 人物与关系 ──
        r = await call("list_characters")
        chars = (r.data or {}).get("characters", [])
        ok5a = r.success and all("role" in c for c in chars)
        r2 = await call("read_character", character_id=chars[0]["id"]) if chars else None
        ok5b = bool(r2 and r2.success and all(
            k in r2.data for k in ("motivation", "arc", "personality", "background")))
        r3 = await call("list_relations")
        check("Q5 人物层答得出：他是谁 / 他想要什么 / 他从哪到哪 / 这两人什么关系",
              ok5a and ok5b and r3.success,
              f"{len(chars)} 人，{(r3.data or {}).get('count', '-')} 条关系，"
              f"motivation={bool((r2.data or {}).get('motivation')) if r2 else '-'} arc={bool((r2.data or {}).get('arc')) if r2 else '-'}")

        # ── 6. 伏笔 ──
        r = await call("list_setups")
        check("Q6 伏笔答得出：还没收的有哪些、埋在哪、打算在哪收",
              r.success and "setups" in r.data,
              f"{(r.data or {}).get('count', '-')} 条伏笔")

        # ── 7. 设计完成到哪、正文断点在哪、下一步做什么 ──
        r = await call("read_progress")
        g = r.data or {}
        check("Q7 进度答得出：设计缺什么 + 正文断点 + 逾期伏笔 + 最近改动 + 下一步",
              r.success and all(k in g for k in (
                  "design_gaps", "content_break", "overdue_setups",
                  "recently_changed_chapters", "next_step", "status_counts")),
              f"缺口 {len(g.get('design_gaps', []))} 条，断点={(g.get('content_break') or {}).get('global_order')}，"
              f"下一步={(g.get('next_step') or {}).get('kind')}")
        gaps = {x["what"] for x in g.get("design_gaps", [])}
        check("Q7b 迁移过来的章「有正文却没写『本章写成了什么』」被点名",
              any("没写「本章写成了什么」" in w for w in gaps),
              f"{len(gaps)} 类缺口")

        # ── 8. 全局内容顺序：从哪一幕到哪一幕、哪一章到哪一章 ──
        orders = [c["global_order"] for c in chs]
        spans_ok = True
        cursor = 1
        for a in acts:
            if a["chapter_count"] == 0:
                continue
            if (a["first_chapter_order"], a["last_chapter_order"]) != (cursor, cursor + a["chapter_count"] - 1):
                spans_ok = False
            cursor += a["chapter_count"]
        check("Q8 全局顺序自洽：幕的起止章号 = 前序各幕章数之和 + 1，且章号连续不跳号",
              ok5a and orders == list(range(1, len(orders) + 1)) and spans_ok,
              f"章号 {orders}，幕跨度连续={spans_ok}")

        # ── 附：搜索范围含正文 ──
        kw = None
        ch1 = next((c for c in chs if c["body_chars"] > 0), None)
        if ch1:
            body = (await db.execute(
                select(Chapter.content).where(Chapter.id == uuid.UUID(ch1["id"]))
            )).scalar_one()
            kw = body[20:24]
        if kw:
            r = await call("search", keyword=kw)
            hit_fields = {m["field"] for h in (r.data or {}).get("hits", []) for m in h["matches"]}
            check("附带 search 的搜索范围含正文（命中 content 字段）",
                  r.success and "content" in hit_fields,
                  f"关键词 {kw!r} 命中字段 {sorted(hit_fields)}")

window = print
print("=" * 78)
print("规格第 7 节 · 八条「必须能回答的问题」验收（真实工具层，跑在已迁移测试库）")
print("=" * 78)
asyncio.run(main())
print("=" * 78)
print(f"FAILURES: {len(FAILS)}" + ("" if not FAILS else f"  -> {FAILS}"))
sys.exit(1 if FAILS else 0)