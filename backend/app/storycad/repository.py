import logging
import uuid
from typing import Any
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer
from app.project.models import Project
from app.storycad.models import Act, Chapter, Character, CharacterRelation, Setup
from app.storycad.entity_map import ENTITY_MAP
from app.storycad.order import order_by_sequence
from app.storycad.sequence import renumber_container
from app.utils import row_to_dict

logger = logging.getLogger(__name__)

# 客户端永远不能通过 update_entity 设置的字段。
#
# 注意：``word_count`` / ``scene_count`` / ``total_words`` 曾经在这里是因为
# 它们是派生统计值；现在这些列已经删掉，字数一律实时算。
#
# ``sort_order`` 也是受保护的：规格第 5 节要求**移动只用「插到谁之后」表达**，
# 序号由实现归一化成连续整数——客户端自己算序号必然算错（历史上就是这么撞出
# 重复序号的）。要移动请走 ``POST …/{entity_type}/{id}/move``，它内部用
# ``storycad/sequence.move_item``。
PROTECTED_FIELDS = frozenset({
    "id",
    "project_id",
    "created_at",
    "updated_at",
    "sort_order",
})

# create_entity 时 id 与 project_id 是合法的（由服务端填），其余仍受保护。
# ``sort_order`` 也要放行：**两条创建路径都必须先调 assign_initial_sort_order
# 把服务端算好的序号写进 data**，再由 create_entity 落库；若在这里把它过滤掉，
# 新行就会全落到模型默认值 0 上并列成一团（客户端传的值仍然无效——会被
# assign_initial_sort_order 覆盖）。
CREATE_PROTECTED = PROTECTED_FIELDS - {"id", "project_id", "sort_order"}


def with_chapter_content(statement):
    """给"要读正文"的章节查询加上显式 undefer。

    ``chapters.content`` 是 **deferred 列**。在 async 会话里**直接读这个属性**
    会触发懒加载，而懒加载要发 IO 却不能 await，SQLAlchemy 于是抛
    ``MissingGreenlet: greenlet_spawn has not been called``。
    所以凡是要读正文的地方，都必须在查询里显式 undefer——**不能靠 getattr**。

    反过来，列出章节的查询**故意不加**它：正文就不会被拖出来。这就是
    "结构性保证"落地的方式。
    """
    return statement.options(undefer(Chapter.content))


# 外键列 → 目标模型映射：写入时必须校验目标行属于同一项目，
# 否则可把章/关系/伏笔挂到他人项目下的行上。
# 这是唯一一份：API 路由与仓储共用它，避免两边各写一份而漂移。
ENTITY_FK_MAP = {
    Chapter: {"act_id": Act},
    CharacterRelation: {"character_id": Character, "target_id": Character},
    Setup: {"planted_in": Chapter, "pay_off_in": Chapter},
}

# 编辑器增量同步接受的实体类型。没有 scenes / edges——两层的表都已删除。
SYNC_ENTITY_TYPES = ("acts", "chapters", "characters", "character_relations", "setups")

# 顺序由服务端归一化的实体类型（见 storycad/sequence.py）。
SEQUENCED_ENTITY_TYPES = ("acts", "chapters")


class AiStoryCADRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ============================================================
    # Editor data: full load
    # ============================================================

    async def get_editor_data(self, project_id: uuid.UUID) -> dict:
        result = {"project_id": str(project_id)}

        async def _fetch(model):
            # 顺序只有一个真相：sort_order。该列没有唯一约束，所以每次排序
            # 都必须追加确定性 tie-break，否则并列行的顺序由 PG 自由决定，
            # 同一项目两次加载可能不一致。
            query = (
                select(model)
                .where(model.project_id == project_id)
                .order_by(*order_by_sequence(model))
            )
            rows = (await self.db.execute(query)).scalars().all()
            return [self._row(obj) for obj in rows]

        # 查询串行执行：AsyncSession 不能并发使用（asyncpg 每个连接只允许
        # 一个操作），asyncio.gather 在这里会抛 InterfaceError。
        result["acts"] = await _fetch(Act)
        # 章节按叙事顺序返回（幕序 → 幕内序号），各视图直接沿用该顺序。
        # 正文列是 deferred 的，所以这里不会把全文拖出来。
        chapters_query = (
            select(Chapter)
            .outerjoin(Act, Act.id == Chapter.act_id)
            .where(Chapter.project_id == project_id)
            .order_by(*order_by_sequence(Act), *order_by_sequence(Chapter))
        )
        chapters = (await self.db.execute(chapters_query)).scalars().all()
        result["chapters"] = [self._row(obj) for obj in chapters]
        result["characters"] = await _fetch(Character)
        result["character_relations"] = await _fetch(CharacterRelation)
        result["setups"] = await _fetch(Setup)

        project = (await self.db.execute(
            select(Project).where(Project.id == project_id)
        )).scalar_one_or_none()
        if project:
            result["project_title"] = project.title or ""
            result["global_settings"] = project.global_settings or ""

        return result

    # ============================================================
    # Chapter content (deferred column, loaded on demand)
    # ============================================================

    async def _owned_chapter(self, chapter_id: uuid.UUID, project_id: uuid.UUID) -> Chapter | None:
        return (await self.db.execute(
            select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
        )).scalar_one_or_none()

    async def get_chapter_content(self, chapter_id: uuid.UUID, project_id: uuid.UUID) -> str | None:
        """取一章正文；章不存在返回 None。

        用 ``first()`` 而不是 ``scalar_one_or_none()``：后者分不清"章不存在"和
        "章存在但正文是 NULL"，而调用方要据此返回 404。
        """
        row = (await self.db.execute(
            select(Chapter.content).where(
                Chapter.id == chapter_id, Chapter.project_id == project_id
            )
        )).first()
        if row is None:
            return None
        return row[0] or ""

    async def get_all_chapter_contents(self, project_id: uuid.UUID) -> dict[str, str]:
        rows = (await self.db.execute(
            select(Chapter.id, Chapter.content).where(Chapter.project_id == project_id)
        )).all()
        return {str(cid): (content or "") for cid, content in rows}

    async def save_chapter_content(
        self, chapter_id: uuid.UUID, project_id: uuid.UUID, content: str
    ) -> bool | None:
        chapter = await self._owned_chapter(chapter_id, project_id)
        if chapter is None:
            return None
        chapter.content = content
        await self.db.flush()
        return True

    # ============================================================
    # Editor data: incremental sync
    # ============================================================

    async def sync_editor_data(self, project_id: uuid.UUID, changes: dict) -> dict:
        touched: set[str] = set()
        for entity_type in SYNC_ENTITY_TYPES:
            ops = changes.get(entity_type, {})
            if not ops:
                continue
            if any(ops.get(key) for key in ("created", "updated", "deleted")):
                touched.add(entity_type)
            for delete_id in ops.get("deleted", []):
                await self._delete_entity(entity_type, delete_id, project_id)
            for item in ops.get("created", []):
                item["project_id"] = project_id
                await self._create_entity(entity_type, item)
            for item in ops.get("updated", []):
                await self._update_entity(entity_type, item, project_id)

        # global_settings 是唯一允许在项目上直接改的字段。
        for item in changes.get("projects", {}).get("updated", []):
            if "global_settings" in item:
                project = (await self.db.execute(
                    select(Project).where(Project.id == project_id)
                )).scalar_one_or_none()
                if project:
                    project.global_settings = item["global_settings"]
                    touched.add("projects")

        if not touched:
            return {"applied": 0, "renumbered": {}}

        # 顺序变了就归一化成连续 1..N。这是实现的责任：客户端各自算
        # max+1 会撞出重复序号，而顺序就是结构本身（规格第 5 节）。
        renumbered: dict[str, int] = {}
        if touched & set(SEQUENCED_ENTITY_TYPES):
            acts_changed = await renumber_container(self.db, Act, project_id)
            if acts_changed:
                renumbered["acts"] = acts_changed
            act_ids = (await self.db.execute(
                select(Act.id).where(Act.project_id == project_id)
            )).scalars().all()
            chapters_changed = 0
            for act_id in act_ids:
                chapters_changed += await renumber_container(
                    self.db, Chapter, project_id, "act_id", act_id
                )
            if chapters_changed:
                renumbered["chapters"] = chapters_changed

        await self.db.commit()
        return {"applied": len(touched), "renumbered": renumbered}

    # ============================================================
    # Per-entity CRUD
    # ============================================================

    async def list_entities(self, model_class: type, project_id: uuid.UUID) -> list[dict]:
        result = await self.db.execute(
            select(model_class).where(model_class.project_id == project_id)
            .order_by(*order_by_sequence(model_class))
        )
        return [self._row(row) for row in result.scalars().all()]

    async def next_sort_order(
        self,
        model_class: type,
        project_id: uuid.UUID,
        parent_column: str | None = None,
        parent_id: uuid.UUID | None = None,
    ) -> int:
        """新行的 ``sort_order`` = 同级容器内最大值 + 1。

        幕按项目编号，章按幕编号。把规则集中在这里，工具层、编辑器同步路径
        和迁移脚本才会一致。
        """
        from sqlalchemy import func

        query = select(func.coalesce(func.max(model_class.sort_order), 0)).where(
            model_class.project_id == project_id
        )
        if parent_column and parent_id is not None:
            query = query.where(getattr(model_class, parent_column) == parent_id)
        max_order = (await self.db.execute(query)).scalar()
        # 1-based：空容器从 1 开始，这也是编辑器乐观插入所假设的。
        return (max_order or 0) + 1

    async def assign_initial_sort_order(
        self, model_class: type, project_id: uuid.UUID, data: dict
    ) -> None:
        """给**新行**定位置：接在所在容器末尾。

        位置是服务端的责任：``sort_order`` 在 ``PROTECTED_FIELDS`` 里，客户端
        传的裸序号一律被丢弃（规格第 5 节），所以两条创建路径——Agent 工具/
        编辑器同步（``_create_entity``）与直接 ``POST /{entity_type}``——都必须
        先调这里，否则新行会全落到模型默认值 0 上、并列成一团。
        """
        if model_class is Chapter:
            act_id = data.get("act_id")
            if act_id:
                data["sort_order"] = await self.next_sort_order(
                    Chapter, project_id, "act_id", uuid.UUID(str(act_id))
                )
        elif model_class is Act:
            data["sort_order"] = await self.next_sort_order(Act, project_id)

    async def get_entity(self, model_class: type, entity_id: uuid.UUID) -> dict | None:
        result = await self.db.execute(select(model_class).where(model_class.id == entity_id))
        row = result.scalar_one_or_none()
        return self._row(row) if row else None

    async def create_entity(self, model_class: type, data: dict, extra_attrs: dict | None = None) -> dict:
        column_names = {col.name for col in model_class.__table__.columns}
        filtered = {k: v for k, v in data.items() if k in column_names and k not in CREATE_PROTECTED}
        for col in model_class.__table__.columns:
            if col.name in filtered and isinstance(filtered[col.name], str) and isinstance(col.type, UUID):
                filtered[col.name] = uuid.UUID(filtered[col.name])
        obj = model_class(**filtered)
        self.db.add(obj)
        await self.db.flush()
        if extra_attrs:
            for key, value in extra_attrs.items():
                setattr(obj, key, value)
        return self._row(obj)

    async def update_entity(self, model_class: type, data: dict) -> dict | None:
        for col in model_class.__table__.columns:
            if col.name in data and isinstance(data[col.name], str) and isinstance(col.type, UUID):
                data[col.name] = uuid.UUID(data[col.name])
        entity_id = data.pop("id", None)
        if isinstance(entity_id, str):
            entity_id = uuid.UUID(entity_id)
        if not entity_id:
            return None
        result = await self.db.execute(select(model_class).where(model_class.id == entity_id))
        obj = result.scalar_one_or_none()
        if not obj:
            return None
        # 用**列名**判断可写性，不用 hasattr：延迟列（chapters.content）在 async
        # 会话里被 hasattr 探测会触发懒加载，抛的是 InvalidRequestError 而不是
        # AttributeError——hasattr 吞不掉它，于是整个请求 500，而这一列本该可写。
        column_names = {col.name for col in model_class.__table__.columns}
        for key, value in data.items():
            if key in PROTECTED_FIELDS:
                continue
            if key in column_names:
                setattr(obj, key, value)
        await self.db.flush()
        return self._row(obj)

    async def delete_entity(self, model_class: type, entity_id: uuid.UUID) -> bool:
        result = await self.db.execute(select(model_class).where(model_class.id == entity_id))
        obj = result.scalar_one_or_none()
        if not obj:
            return False
        await self.db.delete(obj)
        await self.db.flush()
        return True

    # ============================================================
    # Internal helpers
    # ============================================================

    async def _validate_fk_targets(self, model_class: type, data: dict, project_id: uuid.UUID) -> bool:
        # 安全：校验 data 中每个外键均指向同一项目下的行；无效时返回 False，
        # 调用方跳过该写入（与跨项目 update/delete 的静默跳过语义一致）。
        fk_map = ENTITY_FK_MAP.get(model_class)
        if not fk_map:
            return True
        for column_name, target_model in fk_map.items():
            value = data.get(column_name)
            if value is None or value == "":
                continue
            try:
                target_id = uuid.UUID(str(value))
            except (ValueError, TypeError, AttributeError):
                return False
            result = await self.db.execute(
                select(target_model.id).where(
                    target_model.id == target_id,
                    target_model.project_id == project_id,
                )
            )
            if result.scalar_one_or_none() is None:
                return False
        return True

    async def _create_entity(self, entity_type: str, data: dict):
        model_class = ENTITY_MAP.get(entity_type)
        if not model_class:
            return
        project_uuid = uuid.UUID(str(data.get("project_id")))
        if not await self._validate_fk_targets(model_class, data, project_uuid):
            logger.warning(
                "Skipping create of %s: foreign key target not in project %s",
                entity_type, data.get("project_id"),
            )
            return
        # 顺序只有一个真相：新行的位置由服务端决定，与直接 POST 路径共用同一条规则。
        await self.assign_initial_sort_order(model_class, project_uuid, data)
        await self.create_entity(model_class, data)

    async def _update_entity(self, entity_type: str, data: dict, project_id: uuid.UUID):
        model_class = ENTITY_MAP.get(entity_type)
        if not model_class:
            return
        entity_id = data.get("id")
        if entity_id:
            eid = uuid.UUID(entity_id) if isinstance(entity_id, str) else entity_id
            existing = (await self.db.execute(
                select(model_class).where(model_class.id == eid)
            )).scalar_one_or_none()
            if existing is None or existing.project_id != project_id:
                logger.warning(
                    "Skipping update of %s %s: not found or not in project %s",
                    entity_type, eid, project_id,
                )
                return

        # 安全：更新携带的外键列也必须指向同一项目下的行。
        if not await self._validate_fk_targets(model_class, data, project_id):
            logger.warning(
                "Skipping update of %s %s: foreign key target not in project %s",
                entity_type, data.get("id"), project_id,
            )
            return

        # 章正文现在就是 chapters.content 这一列，所以不再需要任何特殊分支：
        # update_entity 直接写它，也不会触发一次多余的正文加载。
        await self.update_entity(model_class, data)

    async def _delete_entity(self, entity_type: str, entity_id_str: str, project_id: uuid.UUID):
        model_class = ENTITY_MAP.get(entity_type)
        if not model_class:
            return
        try:
            entity_id = uuid.UUID(entity_id_str) if isinstance(entity_id_str, str) else entity_id_str
        except (ValueError, AttributeError):
            return
        existing = (await self.db.execute(
            select(model_class).where(model_class.id == entity_id)
        )).scalar_one_or_none()
        if existing is None or existing.project_id != project_id:
            logger.warning(
                "Skipping delete of %s %s: not found or not in project %s",
                entity_type, entity_id, project_id,
            )
            return
        await self.delete_entity(model_class, entity_id)

    @staticmethod
    def _row(obj: Any) -> dict:
        return row_to_dict(obj)
