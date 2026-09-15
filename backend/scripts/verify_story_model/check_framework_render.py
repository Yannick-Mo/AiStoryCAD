import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import select

from app.database import async_session
from app.agent.context import ContextBuilder
from app.agent.context_compressor import estimate_text_tokens
from app.agent.loop import _render_framework_section, _section_for_tool
from app.project.models import Project
from app.storycad.models import Act, Chapter

FAILS = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(label)


class FakeState:
    def __init__(self, ctx, pid):
        self.project_context = ctx
        self.project_id = pid


async def _make_big_project(db, chapters=3000):
    """造一个 N 章的书（**不 commit**，结尾 rollback）。"""
    proj = Project(title="窗口验证书", genre="x", logline="", owner_id=uuid.uuid4())
    db.add(proj)
    await db.flush()
    act = Act(project_id=proj.id, name="唯一幕", purpose="这一幕要干什么", sort_order=1)
    db.add(act)
    await db.flush()
    for i in range(1, chapters + 1):
        db.add(Chapter(
            project_id=proj.id, act_id=act.id, title=f"第{i}章",
            goal="这一章要写什么（" + "情节。" * 30 + "）",
            result="", content=("正文。" * 100) if i <= 10 else "",
            status="draft", sort_order=i,
        ))
    await db.flush()
    return proj


async def main():
    async with async_session() as db:
        proj = (await db.execute(select(Project).order_by(Project.created_at))).scalars().first()
        builder = ContextBuilder(db)

        for depth in ("framework", "summary"):
            summary = await builder.build_summary(proj.id, depth=depth)
            check(f"build_summary(depth={depth!r}) 成功", bool(summary),
                  f"{len(summary.get('acts', []))} 幕 / 角色键 {sorted(summary.keys())[:6]}")
            if depth != "framework":
                continue
            check("framework 深度下章节条目带 body_chars（用于「已写/未写」）",
                  all("body_chars" in c for a in summary["acts"] for c in a["chapters"]))
            check("framework 深度**不含**正文",
                  all("content" not in c for a in summary["acts"] for c in a["chapters"]))
            check("setups 进入了框架缓存", "setups" in summary)
            win = summary.get("chapter_window") or {}
            check("framework 深度带了窗口信息（chapter_window）", bool(win),
                  f"窗口 {win.get('lo')}-{win.get('hi')}，"
                  f"焦点 {(win.get('focus') or {}).get('global_order')}")
            listed = sum(len(a["chapters"]) for a in summary["acts"])
            check("framework 深度**只**给窗口内的章（不再把全书建成条目）",
                  listed <= 2 * int(win.get("radius") or 0) + 2,
                  f"{listed} 条")
            text = _render_framework_section(FakeState(summary, proj.id))
            print("         ---------- 渲染出的常驻地图 ----------")
            for line in text.splitlines()[:16]:
                print("         | " + line[:160])
            check("常驻地图渲染不抛异常且有内容", bool(text),
                  f"{len(text)} 字符 / {estimate_text_tokens(text)} tokens")
            check("幕索引标了章数与全局章号范围", "全书第" in text or "暂无章" in text)
            check("章行标了全局章号 + chapter_id + 已写或未写",
                  "chapter_id=" in text and ("已写" in text or "未写" in text))
            check("常驻地图**不再**携带每章的「要写什么」（那是按需读的细节）",
                  "要写：" not in text)
            check("渲染结果里没有 {{ 占位符", "{{" not in text)

        # 空项目的边界：不该崩
        empty = {"acts": [], "characters": [], "relations": [], "setups": []}
        check("空项目渲染返回空串而不是报错",
              _render_framework_section(FakeState(empty, proj.id)) == "")

        # ── 位置开窗：焦点在哪，窗口就围着哪（3000 章场景；全程不落库）──
        big = await _make_big_project(db, 3000)
        focus_ch = (await db.execute(
            select(Chapter).where(Chapter.project_id == big.id, Chapter.sort_order == 1500)
        )).scalar_one()
        ctx = await builder.build_summary(
            big.id, depth="framework", skip_cache=True, focus_chapter_id=focus_ch.id,
        )
        win = ctx["chapter_window"]
        check("3000 章：窗口围着「用户正在看的那一章」",
              win["lo"] <= 1500 <= win["hi"] and (win["focus"] or {}).get("global_order") == 1500,
              f"窗口 {win['lo']}-{win['hi']}，焦点 {(win['focus'] or {}).get('global_order')}")
        entries = sum(len(a["chapters"]) for a in ctx["acts"])
        check("3000 章：上下文里只有窗口内的章（不是 3000 条）",
              entries <= 2 * int(win["radius"]) + 2, f"{entries} 条")
        text = _render_framework_section(FakeState(ctx, big.id))
        numbers = []
        for line in text.splitlines():
            if line.startswith("- 第"):
                try:
                    numbers.append(int(line.split("章")[0].replace("- 第", "")))
                except ValueError:
                    pass
        check("3000 章：地图里列出的章都在焦点附近",
              bool(numbers) and min(numbers) >= 1500 - int(win["radius"])
              and max(numbers) <= 1500 + int(win["radius"]),
              f"第 {min(numbers)}-{max(numbers)} 章，共 {len(numbers)} 章列出")
        check("3000 章：未列出的章数如实标注，并给出取法",
              "未列出" in text and "全书 3000 章" in text and "read_chapters" in text,
              [line for line in text.splitlines() if "未列出" in line][:1])
        check("3000 章：常驻地图 token 有界（<= 12000）",
              estimate_text_tokens(text) <= 12000, f"{estimate_text_tokens(text)} tokens")
        check("3000 章：焦点那一章在「你在哪」段里出现",
              "第 1500 章" in text and "chapter_id=" in text)
        await db.rollback()

    # _section_for_tool 覆盖新工具集
    cases = {
        "create_act": "structure", "move_chapter": "structure", "update_chapter": "structure",
        "write_chapter": "content", "continue_chapter": "content",
        "create_setup": "setups", "update_setup": "setups", "delete_setup": "setups",
        "create_relation": "characters", "delete_character": "characters",
        "update_project": "project",
    }
    bad = {t: _section_for_tool(t) for t, want in cases.items() if _section_for_tool(t) != want}
    check("_section_for_tool 覆盖全部新工具（缓存失效分区正确）", not bad, str(bad))

print("=" * 78)
print("每轮常驻地图渲染（真实库 + 3000 章场景，不落库）")
print("=" * 78)
asyncio.run(main())
print("=" * 78)
print("FAILURES:", len(FAILS), FAILS if FAILS else "")
sys.exit(1 if FAILS else 0)
