import uuid
from datetime import datetime, UTC
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Project(Base):
    """作品层：这本书是什么、现在写到哪、目前确定了什么。

    字段去留的依据见 docs/故事模型设计规格.md 第 3 节总表。

    已删除（见规格第 10 节）：
    - ``workflow_stage``：全仓只有模型定义和迁移，从未被读写。
    - ``ProjectVersion``：它是编辑器同步游标（快照里只有
      ``{"type": "editor_sync", ...}``），既不是版本也不能回滚。
    - ``ProjectConfig``：只剩目标字数一个字段，不值得单独一张表，
      已并进 ``projects.total_words``。
    """

    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    # ---- 规格总表的 7 个字段 ----
    title = Column(String(255), default="Untitled Project")  # 书名
    genre = Column(String(100), default="")                  # 类型
    logline = Column(Text, default="")                       # 一句话梗概
    # 历史字段：终局已从产品里拿掉，列先保留、不迁移、不删旧数据，也不再读写。
    ending = Column(Text, default="")                        # 终局（已废弃）
    spine = Column(Text, default="")                         # 当前已完成骨架
    global_settings = Column(Text, default="")               # 当前全局设定
    total_words = Column(Integer, default=100000)            # 全书目标字数

    # 规格总表未列入、但仍有消费者的两个历史字段：
    # ``description`` 只有创建对话框在写；``status`` 被首页项目卡的阶段
    # 徽章和进度条读取（frontend/src/pages/home/ProjectGrid.tsx）。
    # 本次重构不动它们（规格没有授权删除），留待专门决定。
    description = Column(Text, default="")
    status = Column(String(20), default="init")

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
