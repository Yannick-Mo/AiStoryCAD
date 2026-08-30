import uuid
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, Column, String, Text, DateTime, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.project.models import Base as StoryBase


# ============================================================
# Narrative structure
# ============================================================

class Act(StoryBase):
    __tablename__ = "acts"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False, default="新幕")
    sort_order = Column(Integer, nullable=False, default=0)
    color = Column(String(20), default="#8b5cf6")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Chapter(StoryBase):
    __tablename__ = "chapters"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    act_id = Column(UUID(as_uuid=True), ForeignKey("acts.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String(200), nullable=False, default="新章")
    goal = Column(Text, default="")
    status = Column(String(20), default="draft")
    sort_order = Column(Integer, nullable=False, default=0)
    scene_count = Column(Integer, default=0)
    total_words = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Scene(StoryBase):
    __tablename__ = "scenes"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_id = Column(UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="新场")
    sort_order = Column(Integer, nullable=False, default=0)
    pov_character = Column(String(100), default="")
    setting = Column(String(200), default="")
    scene_time = Column(String(100), default="")
    summary = Column(Text, default="")
    word_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SceneContent(StoryBase):
    __tablename__ = "scene_contents"
    scene_id = Column(UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, default="")
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ChapterEdge(StoryBase):
    __tablename__ = "chapter_edges"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id = Column(UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True)
    edge_type = Column(String(20), nullable=False, default="timeline")
    label = Column(String(100), default="")
    source_handle = Column(String(20), default="")
    target_handle = Column(String(20), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================
# Characters
# ============================================================

class Character(StoryBase):
    __tablename__ = "characters"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    role = Column(String(30), default="supporting")
    personality = Column(Text, default="")
    appearance = Column(Text, default="")
    background = Column(Text, default="")
    motivation = Column(Text, default="")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CharacterRelation(StoryBase):
    __tablename__ = "character_relations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id = Column(UUID(as_uuid=True), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(UUID(as_uuid=True), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True)
    rel_type = Column(String(30), default="关联")
    label = Column(String(100), default="")
    description = Column(Text, default="")
    trust = Column(Integer, CheckConstraint('trust >= 0 AND trust <= 100'), default=50)
    threat = Column(Integer, CheckConstraint('threat >= 0 AND threat <= 100'), default=50)
    attraction = Column(Integer, CheckConstraint('attraction >= 0 AND attraction <= 100'), default=50)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

