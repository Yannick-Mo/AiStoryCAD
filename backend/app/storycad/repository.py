import logging
import uuid
from datetime import datetime
from typing import Any
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from app.project.models import Project
from app.project.repository import ProjectRepository
from app.storycad.models import (
    Act, Chapter, Scene, SceneContent, ChapterEdge,
    Character, CharacterRelation,
    Theme, ThemeChapter, ChapterRhythm,
)
from app.storycad.entity_map import ENTITY_MAP
from app.utils import row_to_dict

logger = logging.getLogger(__name__)


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


class StoryCADRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ============================================================
    # Editor data: full load
    # ============================================================

    async def get_editor_data(self, project_id: uuid.UUID) -> dict:
        result = {"project_id": str(project_id)}

        async def _fetch(model, order_field: str | None = None):
            q = select(model).where(model.project_id == project_id)
            if order_field and hasattr(model, order_field):
                q = q.order_by(getattr(model, order_field))
            r = await self.db.execute(q)
            return [self._row(o) for o in r.scalars().all()]

        # Queries run sequentially on purpose: AsyncSession is not safe for
        # concurrent use (asyncpg allows only one operation per connection),
        # so asyncio.gather here raised InterfaceError under load.
        result["acts"] = await _fetch(Act, "sort_order")
        result["chapters"] = await _fetch(Chapter, "sort_order")
        result["scenes"] = await _fetch(Scene, "sort_order")
        result["edges"] = await _fetch(ChapterEdge)
        result["characters"] = await _fetch(Character, "sort_order")
        result["character_relations"] = await _fetch(CharacterRelation)
        result["themes"] = await _fetch(Theme, "sort_order")
        result["theme_chapters"] = await _fetch(ThemeChapter)
        result["rhythms"] = await _fetch(ChapterRhythm)

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
                            "character_relations", "themes", "theme_chapters",
                            "rhythms"]:
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

        await self.db.flush()
        await self._recalc_chapter_counts(project_id)
        await self.db.commit()

        project_repo = ProjectRepository(self.db)
        pv = await project_repo.save_version(project_id, {"type": "editor_sync"})
        return pv.version

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

    async def list_entities(self, model_class: type, project_id: uuid.UUID, order_field: str = "sort_order") -> list[dict]:
        result = await self.db.execute(
            select(model_class).where(model_class.project_id == project_id).order_by(getattr(model_class, order_field))
        )
        return [self._row(r) for r in result.scalars().all()]

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

    async def _create_entity(self, entity_type: str, data: dict):
        model_class = ENTITY_MAP.get(entity_type)
        if not model_class:
            return
        extra = {}
        if entity_type == "scenes" and "content" in data:
            content = data.pop("content")
            from app.agent.utils import count_words
            extra["word_count"] = count_words(content)
        await self.create_entity(model_class, data, extra_attrs=extra or None)

    async def _update_entity(self, entity_type: str, data: dict):
        model_class = ENTITY_MAP.get(entity_type)
        if not model_class:
            return
        scene_content = None
        entity_id = data.get("id")
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

    async def _delete_entity(self, entity_type: str, entity_id_str: str):
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
        await self.delete_entity(model_class, entity_id)

    @staticmethod
    def _row(obj: Any) -> dict:
        return row_to_dict(obj)
