import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from app.project.models import Project
from app.project.repository import ProjectRepository
from app.storycad.models import (
    Act, Chapter, Scene, SceneContent, ChapterEdge,
    Character, CharacterRelation,
)
from app.storycad.entity_map import ENTITY_MAP
from app.storycad.order import order_by_sequence
from app.utils import row_to_dict

logger = logging.getLogger(__name__)

# Editor autosaves are throttled to at most one version row per window, so
# keystroke-level syncs stop flooding project_versions with empty snapshots.
EDITOR_VERSION_THROTTLE_S = 300


# Fields that clients must never be able to set via update_entity.
# - id: lookup key only, never writable
# - project_id: prevents cross-project injection after ownership check
# - created_at / updated_at: server-managed timestamps
# - word_count (Scene), scene_count / total_words (Chapter): server-computed statistics
PROTECTED_FIELDS = frozenset({
    "id",
    "project_id",
    "created_at",
    "updated_at",
    "word_count",
    "scene_count",
    "total_words",
})

# Subset for create_entity – id and project_id are legitimate at creation
# time, but stats and timestamps must remain server-managed.
CREATE_PROTECTED = PROTECTED_FIELDS - {"id", "project_id"}


# 外键列 → 目标模型映射：写入时必须校验目标行属于同一项目，
# 否则可把场景/边/关系挂到他人项目下的章节/角色上。
ENTITY_FK_MAP = {
    Chapter: {"act_id": Act},
    Scene: {"chapter_id": Chapter},
    ChapterEdge: {"source_id": Chapter, "target_id": Chapter},
    CharacterRelation: {"character_id": Character, "target_id": Character},
}


class AiStoryCADRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ============================================================
    # Editor data: full load
    # ============================================================

    async def get_editor_data(self, project_id: uuid.UUID) -> dict:
        result = {"project_id": str(project_id)}

        async def _fetch(model):
            # 顺序只有一个真相：sort_order。该列没有唯一约束，历史数据可能有
            # 重复值，所以每次排序都必须追加确定性 tie-break，否则并列行的顺序
            # 由 PG 自由决定，同一项目两次加载可能不一致。
            q = select(model).where(model.project_id == project_id)
            q = q.order_by(*order_by_sequence(model))
            r = await self.db.execute(q)
            return [self._row(o) for o in r.scalars().all()]

        # Queries run sequentially on purpose: AsyncSession is not safe for
        # concurrent use (asyncpg allows only one operation per connection),
        # so asyncio.gather here raised InterfaceError under load.
        result["acts"] = await _fetch(Act)
        # 章节按叙事顺序返回（幕序 → 幕内序号），各视图直接沿用该顺序。
        chapters_q = (
            select(Chapter)
            .outerjoin(Act, Act.id == Chapter.act_id)
            .where(Chapter.project_id == project_id)
            .order_by(*order_by_sequence(Act), *order_by_sequence(Chapter))
        )
        chapters_r = await self.db.execute(chapters_q)
        result["chapters"] = [self._row(o) for o in chapters_r.scalars().all()]
        result["scenes"] = await _fetch(Scene)
        result["edges"] = await _fetch(ChapterEdge)
        result["characters"] = await _fetch(Character)
        result["character_relations"] = await _fetch(CharacterRelation)

        proj_result = await self.db.execute(
            select(Project).where(Project.id == project_id)
        )
        proj = proj_result.scalar_one_or_none()
        if proj:
            result["project_title"] = proj.title or ""
            result["global_settings"] = proj.global_settings or ""

        return result

    # ============================================================
    # Editor data: incremental sync
    # ============================================================

    async def sync_editor_data(self, project_id: uuid.UUID, changes: dict) -> int:
        has_changes = False
        for entity_type in ["acts", "chapters", "scenes", "edges", "characters",
                            "character_relations"]:
            ops = changes.get(entity_type, {})
            if not ops:
                continue
            if any(ops.get(k) for k in ("created", "updated", "deleted")):
                has_changes = True
            for delete_id in ops.get("deleted", []):
                await self._delete_entity(entity_type, delete_id, project_id)
            for item in ops.get("created", []):
                item["project_id"] = project_id
                await self._create_entity(entity_type, item)
            for item in ops.get("updated", []):
                await self._update_entity(entity_type, item, project_id)

        # Handle global_settings separately (only field allowed on Project)
        projects_ops = changes.get("projects", {})
        for item in projects_ops.get("updated", []):
            if "global_settings" in item:
                result = await self.db.execute(select(Project).where(Project.id == project_id))
                proj = result.scalar_one_or_none()
                if proj:
                    proj.global_settings = item["global_settings"]
                    has_changes = True

        if not has_changes:
            return 0

        # 编辑器变更 + 版本行在一个事务里提交:flush → recalc → version row →
        # 统一 commit,失败统一回滚。
        await self.db.flush()
        await self._recalc_chapter_counts(project_id)

        project_repo = ProjectRepository(self.db)
        latest = await project_repo.latest_version(project_id)
        now = datetime.now(timezone.utc)
        if latest is None or (now - latest.created_at) > timedelta(seconds=EDITOR_VERSION_THROTTLE_S):
            pv = await project_repo.append_version(
                project_id, {"type": "editor_sync", "updated_at": now.isoformat()}
            )
            version = pv.version
        else:
            version = latest.version
        await self.db.commit()
        return version

    async def _recalc_chapter_counts(self, project_id: uuid.UUID):
        counts = await self.db.execute(
            select(Scene.chapter_id, func.count(Scene.id), func.coalesce(func.sum(Scene.word_count), 0))
            .where(Scene.project_id == project_id)
            .group_by(Scene.chapter_id)
        )
        for row in counts.all():
            await self.db.execute(
                Chapter.__table__.update().where(Chapter.id == row[0])
                .values(scene_count=row[1], total_words=row[2])
            )
        chapters_without_scenes = await self.db.execute(
            select(Chapter.id).where(Chapter.project_id == project_id)
            .where(~Chapter.id.in_(select(Scene.chapter_id).where(Scene.project_id == project_id)))
        )
        for (cid,) in chapters_without_scenes.all():
            await self.db.execute(
                Chapter.__table__.update().where(Chapter.id == cid)
                .values(scene_count=0, total_words=0)
            )

    async def recalc_chapter(self, chapter_id: uuid.UUID):
        """Refresh one chapter's scene_count/total_words from its scenes.

        Cheap single-chapter variant of ``_recalc_chapter_counts`` for the
        agent write path (a body write only affects its own chapter).
        """
        counts = await self.db.execute(
            select(func.count(Scene.id), func.coalesce(func.sum(Scene.word_count), 0))
            .where(Scene.chapter_id == chapter_id)
        )
        row = counts.one()
        await self.db.execute(
            Chapter.__table__.update().where(Chapter.id == chapter_id)
            .values(scene_count=row[0], total_words=row[1])
        )

    # ============================================================
    # Scene content (separate, lazy-loaded)
    # ============================================================

    async def get_scene_content(self, scene_id: uuid.UUID, project_id: uuid.UUID) -> str | None:
        scene = await self.db.execute(
            select(Scene).where(Scene.id == scene_id, Scene.project_id == project_id)
        )
        if not scene.scalar_one_or_none():
            return None
        result = await self.db.execute(
            select(SceneContent).where(SceneContent.scene_id == scene_id)
        )
        sc = result.scalar_one_or_none()
        return sc.content if sc else None

    async def get_all_scene_contents(self, project_id: uuid.UUID) -> dict[str, str]:
        result = await self.db.execute(
            select(SceneContent).where(SceneContent.project_id == project_id)
        )
        return {str(sc.scene_id): sc.content for sc in result.scalars().all()}

    async def save_scene_content(self, scene_id: uuid.UUID, project_id: uuid.UUID, content: str):
        # Verify scene belongs to project
        scene = await self.db.execute(
            select(Scene).where(Scene.id == scene_id, Scene.project_id == project_id)
        )
        if not scene.scalar_one_or_none():
            return None
        result = await self.db.execute(
            select(SceneContent).where(SceneContent.scene_id == scene_id)
        )
        sc = result.scalar_one_or_none()
        if sc:
            sc.content = content
        else:
            self.db.add(SceneContent(scene_id=scene_id, project_id=project_id, content=content))
        await self.db.flush()
        return True

    # ============================================================
    # Per-entity CRUD
    # ============================================================

    async def list_entities(self, model_class: type, project_id: uuid.UUID) -> list[dict]:
        result = await self.db.execute(
            select(model_class).where(model_class.project_id == project_id)
            .order_by(*order_by_sequence(model_class))
        )
        return [self._row(r) for r in result.scalars().all()]

    async def next_sort_order(
        self,
        model_class: type,
        project_id: uuid.UUID,
        parent_column: str | None = None,
        parent_id: uuid.UUID | None = None,
    ) -> int:
        """New row's ``sort_order`` = max within its container + 1.

        Chapters are numbered project-wide (the value stays unique across acts)
        and scenes are numbered per chapter.  Centralising the rule here keeps
        the agent tools, the editor sync path and the migration script in
        agreement.
        """
        q = select(func.coalesce(func.max(model_class.sort_order), -1)).where(
            model_class.project_id == project_id
        )
        if parent_column and parent_id is not None:
            q = q.where(getattr(model_class, parent_column) == parent_id)
        result = await self.db.execute(q)
        max_order = result.scalar()
        # 1-based: an empty container starts at 1, which is also what the
        # editor's optimistic insert assumes.
        return (max_order if max_order is not None and max_order >= 0 else 0) + 1

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
            for k, v in extra_attrs.items():
                setattr(obj, k, v)
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
        for key, value in data.items():
            if key in PROTECTED_FIELDS:
                continue
            if hasattr(obj, key):
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
        # 顺序只有一个真相：新行的位置由服务端决定（章节接在幕末尾、场景接在
        # 章末尾），否则每个客户端各自算 max+1 会撞出重复的 sort_order。
        if entity_type == "chapters":
            act_id = data.get("act_id")
            if act_id:
                data["sort_order"] = await self.next_sort_order(
                    Chapter, project_uuid, "act_id", uuid.UUID(str(act_id))
                )
        elif entity_type == "scenes":
            chapter_id = data.get("chapter_id")
            if chapter_id:
                data["sort_order"] = await self.next_sort_order(
                    Scene, project_uuid, "chapter_id", uuid.UUID(str(chapter_id))
                )
        extra = {}
        if entity_type == "scenes" and "content" in data:
            content = data.pop("content")
            from app.agent.utils import count_words
            extra["word_count"] = count_words(content)
        await self.create_entity(model_class, data, extra_attrs=extra or None)

    async def _update_entity(self, entity_type: str, data: dict, project_id: uuid.UUID):
        model_class = ENTITY_MAP.get(entity_type)
        if not model_class:
            return
        entity_id = data.get("id")
        if entity_id:
            if isinstance(entity_id, str):
                eid = uuid.UUID(entity_id)
            else:
                eid = entity_id
            result = await self.db.execute(select(model_class).where(model_class.id == eid))
            existing = result.scalar_one_or_none()
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

        scene_content = None
        if entity_type == "scenes" and "content" in data:
            scene_content = data.pop("content")
        await self.update_entity(model_class, data)
        if entity_type == "scenes" and scene_content is not None and entity_id:
            if isinstance(entity_id, str):
                entity_id = uuid.UUID(entity_id)
            result = await self.db.execute(select(Scene).where(Scene.id == entity_id))
            obj = result.scalar_one_or_none()
            if obj:
                from app.agent.utils import count_words
                obj.word_count = count_words(scene_content)
                sc_result = await self.db.execute(select(SceneContent).where(SceneContent.scene_id == entity_id))
                sc = sc_result.scalar_one_or_none()
                if sc:
                    sc.content = scene_content
                else:
                    self.db.add(SceneContent(scene_id=entity_id, project_id=obj.project_id, content=scene_content))

    async def _delete_entity(self, entity_type: str, entity_id_str: str, project_id: uuid.UUID):
        model_class = ENTITY_MAP.get(entity_type)
        if not model_class:
            return
        try:
            if isinstance(entity_id_str, str):
                entity_id = uuid.UUID(entity_id_str)
            else:
                entity_id = entity_id_str
        except (ValueError, AttributeError):
            return
        # Verify the entity belongs to the requesting project
        result = await self.db.execute(select(model_class).where(model_class.id == entity_id))
        existing = result.scalar_one_or_none()
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
