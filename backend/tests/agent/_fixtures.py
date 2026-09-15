"""测试用的构造工厂：常驻地图上下文（``depth="framework"`` 的输出形状）。

为什么要它：``_render_framework_section`` 读的是**构建器的输出形状**——
幕图靠 ``chapter_count`` / ``first_chapter_order``，章目录靠 ``chapter_window``
加窗口内的 ``chapters``。手写一堆半截 fixture 会让测试验的**不是真实契约**，
所以这里按真形状造。
"""
from __future__ import annotations


def framework_context(
    acts,
    *,
    focus_order: int | None = None,
    view_focus: bool = False,
    characters=(),
    relations=(),
    setups=(),
    total_chapter_count: int | None = None,
    first_order: int = 1,
    window_span: tuple[int, int] | None = None,
    content_break_order: int | None = None,
) -> dict:
    """造一个与 ``_build_windowed_framework`` 同形的常驻地图。

    ``acts``：``[{"id"?, "name"?, "purpose"?, "chapters": [{"id"?, "title"?,
    "written"?, "status"?}]}]``。**每个 act 的 chapters 就是"窗口内的章"**
    （真实构建器只把窗口内的章放进来）。

    ``total_chapter_count``：全书章数（默认 = 窗口里这些章）。比窗口大时，
    渲染器必须如实标注"其余在哪"。
    ``first_order``：窗口第一章的全局章号（造"你在写第 2000 章"这种场景用）。
    ``window_span``：窗口的起止全局章号（默认取窗口首尾）。
    """
    out_acts: list[dict] = []
    number = first_order
    last = first_order - 1
    chapters_flat: list[dict] = []
    for i, act in enumerate(acts, 1):
        chapters = []
        first = None
        for ch in act.get("chapters", []):
            first = first if first is not None else number
            chapters.append({
                "id": ch.get("id") or f"c{number}",
                "global_order": number,
                "title": ch.get("title") or f"第{number}章",
                "status": ch.get("status") or "draft",
                "body_chars": 10 if ch.get("written") else 0,
            })
            chapters_flat.append(chapters[-1])
            last = number
            number += 1
        out_acts.append({
            "id": act.get("id") or f"a{i}",
            "name": act.get("name") or f"第{i}幕",
            "sort_order": act.get("sort_order", i),
            "purpose": act.get("purpose") or "",
            "chapter_count": len(chapters),
            "first_chapter_order": first,
            "last_chapter_order": (first + len(chapters) - 1) if chapters else None,
            "chapters": chapters,
        })

    total = total_chapter_count if total_chapter_count is not None else (
        last if chapters_flat else 0
    )

    focus = None
    if focus_order is not None:
        for ch in chapters_flat:
            if ch["global_order"] == focus_order:
                focus = {**ch, "source": "view" if view_focus else "content_break",
                         "is_written": ch["body_chars"] > 0}
                break
    content_break = None
    if content_break_order is not None:
        for ch in chapters_flat:
            if ch["global_order"] == content_break_order:
                content_break = ch
                break

    lo, hi = window_span or (
        (chapters_flat[0]["global_order"], chapters_flat[-1]["global_order"])
        if chapters_flat else (0, 0)
    )
    return {
        "project": {
            "id": "p1", "title": "测试书", "genre": "", "logline": "",
            "spine": "", "spine_chars": 0, "ending": "", "ending_chars": 0,
            "global_settings": "", "global_settings_chars": 0,
            "total_words": 0, "status": "",
        },
        "acts": out_acts,
        "chapter_count": total,
        "written_chapters": sum(1 for c in chapters_flat if c["body_chars"] > 0),
        "chapter_window": {
            "lo": lo, "hi": hi, "radius": 120, "listed": len(chapters_flat),
            "focus": focus, "content_break": content_break,
        },
        "characters": list(characters),
        "relations": list(relations),
        "setups": list(setups),
        "available_skills": [],
    }
