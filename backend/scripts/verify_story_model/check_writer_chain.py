import asyncio
import sys

sys.path.insert(0, "/app")

from sqlalchemy import select

from app.database import async_session
from app.agent.context import ContextBuilder
from app.agent.prompts import render_prompt
from app.project.models import Project
from app.storycad.models import Chapter

FAILS = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(label)


async def main():
    async with async_session() as db:
        proj = (await db.execute(select(Project).order_by(Project.created_at))).scalars().first()
        chapters = (await db.execute(
            select(Chapter).where(Chapter.project_id == proj.id).order_by(Chapter.sort_order)
        )).scalars().all()
        # 挑第二个章：它必须能拿到"上一章写成了什么"
        ch = chapters[1] if len(chapters) > 1 else chapters[0]
        print(f"project={proj.title!r}  章={ch.title!r}（幕内第 {ch.sort_order}）\n")

        builder = ContextBuilder(db)
        ctx = await builder.build_for_writing(ch.id, "continue")
        check("build_for_writing 给出章级上下文",
              bool(ctx) and "chapter_title" in ctx, f"{len(ctx)} 个键")

        check("上一章进入了写手上下文（跨章接缝的载体）",
              "previous_chapter_result" in ctx and "previous_chapter_title" in ctx,
              f"上一章 = {ctx.get('previous_chapter_title')!r}")

        check("全书走向（主线骨架 / 终局）在上下文里有位置",
              all(k in ctx for k in ("spine", "ending", "act_purpose", "logline")),
              f"spine={ctx.get('spine')!r} ending={ctx.get('ending')!r}")

        check("人物「他想要什么」文案已拼好",
              bool(ctx.get("characters_text")),
              f"{len(ctx.get('characters_text') or '')} 字符")

        # 完全按 WritingAgent.run 的方式渲染：persona 来自 persona_writer
        persona = render_prompt("persona_writer") or ""
        kwargs = {"persona": persona, "user_prompt": "请续写这一章的正文。", **ctx}
        rendered = render_prompt("writer", **kwargs)
        check("writer.yaml 渲染成功且无未替换占位符",
              bool(rendered) and "{{" not in rendered and "}}" not in rendered,
              f"{len(rendered)} 字符")

        check("渲染结果里含章标题、幕、上一章等信息",
              (ch.title or "x") in rendered and (ctx.get("act_name") or "x") in rendered,
              "")

        for bad in ("{{", "}}", "scene_title", "scene_summary", "pov_character", "chapter_scenes_framework"):
            if bad in rendered:
                print(f"         ! 渲染结果里仍有 {bad!r}")

        print("         ---------- 渲染结果前 16 行 ----------")
        for line in rendered.splitlines()[:16]:
            print("         | " + line[:150])

print("=" * 78)
print("写手链路复验：build_for_writing + writer.yaml（真实库 + 按 Agent 的方式注入 persona）")
print("=" * 78)
asyncio.run(main())
print("=" * 78)
print("FAILURES:", len(FAILS), FAILS if FAILS else "")
sys.exit(1 if FAILS else 0)