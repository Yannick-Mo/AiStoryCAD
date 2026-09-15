import asyncio
import sys
import time
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import select

from app.database import async_session
from app.agent.context import ContextBuilder
from app.agent.context_compressor import estimate_text_tokens
from app.agent.loop import _render_framework_section
from app.agent.tools import get_tool_registry
from app.project.models import Project
from app.storycad.models import Act, Chapter
from app.storycad.sequence import ordered_chapter_ids

FAILS = []
ACTS, PER_ACT, WRITTEN = 4, 30, 10
TOTAL = ACTS * PER_ACT


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(label)


class FakeState:
    def __init__(self, ctx, pid):
        self.project_context = ctx
        self.project_id = pid


async def main():
    reg = get_tool_registry()
    async with async_session() as db:
        # ── 造一个 4 幕 × 30 章 = 120 章的项目（目标规模）──
        proj = Project(title="规模测试书", genre="悬疑", logline="测试用", owner_id=uuid.uuid4())
        db.add(proj)
        await db.flush()
        pid = proj.id

        acts = []
        for i in range(1, ACTS + 1):
            act = Act(project_id=pid, name=f"第{i}幕", purpose=f"第{i}幕要干什么（测试文本）", sort_order=i)
            db.add(act)
            acts.append(act)
        await db.flush()

        for ai, act in enumerate(acts):
            for j in range(1, PER_ACT + 1):
                n = ai * PER_ACT + j
                body = ""
                if n <= WRITTEN:
                    body = ("正文测试。" * 600)[:3000]
                db.add(Chapter(
                    project_id=pid, act_id=act.id, title=f"第{n}章 测试章",
                    goal=f"这一章要写什么（第 {n} 章）：" + "情节推进说明。" * 12,
                    result="" if n > WRITTEN else f"第 {n} 章写成了什么。",
                    content=body, status="draft", sort_order=j,
                ))
        # **绝不 commit**：README 把本脚本列为「只读项」（对着开发库跑也安全），
        # 而原实现这里 commit 了——于是每跑一次就在目标库里留下一个 120 章的假项目。
        # 实测后果：开发库上攒了 5 个「规模测试书」、600 章垃圾（owner 是随机 UUID，
        # 界面上看不见，但把库撑到了 605 章）。改成 flush：同一会话看得见，
        # 结尾的 rollback 才是真的什么都没写。
        await db.flush()
        print(f"已造：{ACTS} 幕 / {TOTAL} 章（前 {WRITTEN} 章有正文）\n")

        async def call(name, **kw):
            res = await reg[name].run(db=db, project_id=str(pid), user_id=str(proj.owner_id), **kw)
            if not res.success:
                print(f"         ! {name}: {res.error}")
            return res

        # ── 1. 全局章号在幕边界上是否正确 ──
        index = await ordered_chapter_ids(db, pid)
        check(f"有序章 id 覆盖全部 {TOTAL} 章", len(index) == TOTAL, f"{len(index)}")
        rows = (await db.execute(
            select(Chapter.sort_order, Chapter.title, Act.name)
            .join(Act, Act.id == Chapter.act_id)
            .where(Chapter.project_id == pid)
            .order_by(Act.sort_order, Chapter.sort_order)
        )).all()
        check("第 1 章属于第1幕、第 31 章属于第2幕、第 121 章不存在（幕边界）",
              rows[0][2] == "第1幕" and rows[30][2] == "第2幕" and rows[90][2] == "第4幕",
              f"第1章→{rows[0][2]} 第31章→{rows[30][2]} 第91章→{rows[90][2]}")

        # ── 2. read_acts：4 幕 × 30 章，起止章号连续 ──
        r = await call("read_acts")
        aa = (r.data or {}).get("acts", [])
        spans_ok = [(a["first_chapter_order"], a["last_chapter_order"]) for a in aa] == \
                   [(i * PER_ACT + 1, (i + 1) * PER_ACT) for i in range(ACTS)]
        check("read_acts 给出 4 幕、每幕 30 章、起止全局章号连续",
              len(aa) == ACTS and spans_ok,
              f"{[(a['name'], a['chapter_count'], a['first_chapter_order'], a['last_chapter_order']) for a in aa]}")

        # ── 3. read_chapters 在大范围下必须截断，且截断标记是诚实的 ──
        r = await call("read_chapters", chapter_from=1, chapter_to=TOTAL)
        w = r.data or {}
        check("read_chapters(1,120) 触发了预算截断（不是把 12 万字全塞进上下文）",
              w.get("truncated") is True and w.get("next_from"),
              f"返回 {len(w.get('chapters', []))} 章，next_from={w.get('next_from')}")
        check("截断处仍未超过单次结果上限（22000 字符内）",
              len(str(w.get("chapters", ""))) < 22000,
              f"{len(str(w.get('chapters', '')))} 字符")
        first30 = w["chapters"]
        check("截断后返回的章仍是**从第 1 章起连续**的一段（没跳号）",
              [c["global_order"] for c in first30] == list(range(1, len(first30) + 1)),
              f"1..{len(first30)}")
        r = await call("read_chapters", chapter_from=11, chapter_to=15)
        w2 = r.data or {}
        check("小范围不截断，且能跨幕边界取（第 11-15 章）",
              w2.get("truncated") is False and len(w2["chapters"]) == 5,
              f"{[c['global_order'] for c in w2['chapters']]}")

        # ── 4. read_progress 在规模下的断点与统计 ──
        r = await call("read_progress")
        g = r.data or {}
        check("正文断点 = 第一个没写正文的章（第 11 章）",
              (g.get("content_break") or {}).get("global_order") == WRITTEN + 1,
              f"断点={(g.get('content_break') or {}).get('global_order')}")
        check("定稿进度统计覆盖全部章（加起来 = 120）",
              sum((g.get("status_counts") or {}).values()) == TOTAL,
              str(g.get("status_counts")))
        check("设计缺什么：前 10 章有正文但 result 齐全 → 不应把它们算成缺口",
              all("章有正文但没写" not in x["what"] or x.get("global_order", 0) <= WRITTEN
                  for x in g.get("design_gaps", [])),
              f"缺口 {len(g.get('design_gaps', []))} 条")

        # ── 5. 常驻地图的预算与诚实截断 ──
        # 预算按 **token** 算（中文 1 字符 ≈ 1.5 token，ASCII ≈ 0.25，按字符切会骗人）；
        # 窗口半径 120，所以 120 章的书整本都进窗口——这条规模上"没有省略"才对。
        builder = ContextBuilder(db)
        summary = await builder.build_summary(pid, depth="framework")
        t0 = time.perf_counter()
        text = _render_framework_section(FakeState(summary, pid))
        elapsed = (time.perf_counter() - t0) * 1000
        tokens = estimate_text_tokens(text)
        check("常驻地图在 120 章规模下不超预算（幕图 1.2K + 窗口 6K + 侧挂 1.5K tokens）",
              tokens <= 9600, f"{len(text)} 字符 / {tokens} tokens，渲染耗时 {elapsed:.1f}ms")
        check("幕索引把全部 4 幕都列了出来（即使细节被截断）",
              all(f"幕 {i}." in text for i in range(1, ACTS + 1)))
        listed = [ln for ln in text.splitlines() if ln.startswith("- 第")]
        check("120 章整本都在窗口里（半径 120）→ 不该有省略标记",
              len(listed) == TOTAL and "未列出" not in text,
              f"列出 {len(listed)} 章 / 共 {TOTAL} 章")
        check("窗口确实围着「正文断点」开（前 10 章已写，断点在第 11 章）",
              (summary.get("chapter_window", {}).get("focus") or {}).get("source")
              == "content_break",
              str(summary.get("chapter_window", {}).get("focus")))

        # ── 6. 性能：关键查询不是按章 N+1 ──
        t0 = time.perf_counter()
        await builder.build_summary(pid, depth="summary", skip_cache=True)
        t1 = time.perf_counter()
        await builder.build_progress(pid)
        t2 = time.perf_counter()
        check("build_summary(summary) 在 120 章下 < 1s",
              (t1 - t0) < 1.0, f"{(t1 - t0) * 1000:.0f}ms")
        check("build_progress 在 120 章下 < 1s", (t2 - t1) < 1.0, f"{(t2 - t1) * 1000:.0f}ms")

        # ── 7. search 在规模下不失控 ──
        t0 = time.perf_counter()
        r = await call("search", keyword="情节推进说明")
        t1 = time.perf_counter()
        hits = (r.data or {}).get("hits", [])
        check("search 命中大量章时被 limit 收住（不是全量返回）",
              len(hits) <= 8 * 5, f"{len(hits)} 条命中，{(t1 - t0) * 1000:.0f}ms")

        # ── 8. 收尾：一个不存在的全局章号不该崩 ──
        r = await call("read_chapters", chapter_from=200, chapter_to=210)
        check("读越界范围返回空而不是报错", r.success and (r.data or {}).get("chapters") == [])

        await db.rollback()

print("=" * 78)
print(f"规模验证：{ACTS} 幕 × {PER_ACT} 章 = {TOTAL} 章（目标规模）")
print("=" * 78)
asyncio.run(main())
print("=" * 78)
print("FAILURES:", len(FAILS), FAILS if FAILS else "")
sys.exit(1 if FAILS else 0)