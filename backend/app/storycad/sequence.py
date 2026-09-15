"""顺序规范（docs/故事模型设计规格.md 第 5 节）的实现。

删掉所有"线"之后，顺序不再只是排序，**它就是结构本身**：

1. 位置的真相只有一份：层内序号 + 父子归属。
   全局章号 = 前序各幕的章数之和 + 幕内序号（算出来的，不存）。
2. 归属可改：章能换幕。
3. 移动用"插到谁之后"表达：``移到 X 之后`` / ``移到开头``。
   工具层永不暴露裸序号——让模型算序号必错。
4. 同一父级下的序号由实现归一化成连续整数。
   这是实现的责任，不是模型的责任，也不是用户的责任。

本模块取代了 ``storycad/timeline.py``。旧的思路是"把顺序投影成一条主时序线
（timeline 边）"，但顺序本来就是唯一真相，不需要再存一份投影——需要的是
在移动之后把它归一化。所以投影变成了维护。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storycad.order import order_by_sequence


def container_filter(
    model: Any,
    project_id: uuid.UUID,
    container_column: str | None,
    container_id: uuid.UUID | None,
) -> list[Any]:
    """同级容器的筛选条件。``container_column=None`` 表示整个项目就是一个容器（幕）。"""
    conditions = [model.project_id == project_id]
    if container_column:
        conditions.append(getattr(model, container_column) == container_id)
    return conditions


async def list_siblings(
    db: AsyncSession,
    model: Any,
    project_id: uuid.UUID,
    container_column: str | None = None,
    container_id: uuid.UUID | None = None,
) -> list[Any]:
    """按当前顺序取出同级行。"""
    query = select(model).where(*container_filter(model, project_id, container_column, container_id))
    query = query.order_by(*order_by_sequence(model))
    return list((await db.execute(query)).scalars().all())


async def renumber_container(
    db: AsyncSession,
    model: Any,
    project_id: uuid.UUID,
    container_column: str | None = None,
    container_id: uuid.UUID | None = None,
) -> int:
    """把同级行的 ``sort_order`` 归一化成连续 1..N，返回实际改动的行数。"""
    changed = 0
    for index, row in enumerate(
        await list_siblings(db, model, project_id, container_column, container_id), start=1
    ):
        if row.sort_order != index:
            row.sort_order = index
            changed += 1
    if changed:
        await db.flush()
    return changed


async def move_item(
    db: AsyncSession,
    model: Any,
    project_id: uuid.UUID,
    *,
    item_id: uuid.UUID,
    after_id: uuid.UUID | None = None,
    position: str = "end",
    container_column: str | None = None,
    container_id: uuid.UUID | None = None,
) -> Any:
    """把 ``item_id`` 放进目标容器并重新定位。

    规格第 5 节要求移动只能有三种说法，这里就是那三种：

    * ``after_id`` 给了 → **插到它之后**；
    * 否则 ``position="end"``（默认）→ 接在**末尾**；
    * 否则 ``position="start"`` → 插到**开头**。

    一次调用同时完成两件事：改归属（换幕）和重排。容器内序号随后归一化成
    连续 1..N；如果换了容器，原容器也会被归一化（否则会留下一个空位）。

    返回值是被移动的 ORM 行，调用方可以直接读它新的 ``sort_order``。
    """
    item = await db.get(model, item_id)
    if item is None:
        raise LookupError(f"{model.__name__} {item_id} 不存在")

    old_container_id = getattr(item, container_column) if container_column else None
    changes_container = container_column is not None and old_container_id != container_id

    siblings = [
        row for row in
        await list_siblings(db, model, project_id, container_column, container_id)
        if row.id != item_id
    ]

    sibling_ids = [row.id for row in siblings]
    if after_id is not None:
        if after_id not in sibling_ids:
            raise LookupError(
                f"after_id 指定的 {model.__name__} {after_id} 不在目标容器里"
            )
        insert_at = sibling_ids.index(after_id) + 1
    elif position == "start":
        insert_at = 0
    else:
        insert_at = len(siblings)

    siblings.insert(insert_at, item)
    if container_column:
        setattr(item, container_column, container_id)

    for index, row in enumerate(siblings, start=1):
        row.sort_order = index
    await db.flush()

    if changes_container:
        # 离开的那个容器现在少了一行，序号会留下空位。
        await renumber_container(db, model, project_id, container_column, old_container_id)

    return item


async def ordered_chapter_ids(db: AsyncSession, project_id: uuid.UUID) -> list[uuid.UUID]:
    """全部章节 id，按故事顺序（幕序 → 幕内序号）。

    全局章号就是这里的下标 + 1——它是算出来的，不存（规格判据②）。
    act_id 是 NOT NULL 的，所以内连接不会丢章。
    """
    from app.storycad.models import Act, Chapter

    result = await db.execute(
        select(Chapter.id)
        .join(Act, Act.id == Chapter.act_id)
        .where(Chapter.project_id == project_id)
        .order_by(Act.sort_order.asc(), Chapter.sort_order.asc(), *order_by_sequence(Chapter))
    )
    return [row for (row,) in result.all()]
