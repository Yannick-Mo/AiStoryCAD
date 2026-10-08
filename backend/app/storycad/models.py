import uuid
from datetime import datetime, UTC
from sqlalchemy import CheckConstraint, Column, String, Text, DateTime, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import deferred
from app.project.models import Base as StoryBase


# ============================================================
# 顺序脊柱：项目 → 幕 → 章（章是最底层，也是正文层）
#
# 场景层已删除：原来的最小写作单位是"场"，一章 3-4 场，一本 100 章的
# 书约 400 个接缝且章内接缝几乎没人管。现在最小单位是章，接缝降到约
# 100 个，每一个都由**上一章的结尾原文**对接（不是摘要——摘要是要复述的
# 派生字段，用户手改正文之后它就开始过期，且没有东西会重算它）。
# 见 docs/故事模型设计规格.md 第 6 节。
# ============================================================

class Act(StoryBase):
    __tablename__ = "acts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False, default="新幕")     # 幕名
    purpose = Column(Text, default="")                            # 这一幕要干什么
    sort_order = Column(Integer, nullable=False, default=0)       # 幕序
    color = Column(String(20), default="#8b5cf6")                 # 仅供前端显示
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class Chapter(StoryBase):
    """章：计划（要写什么）+ 正文。

    **没有「本章写成了什么」**（迁移 0022 删除）：那是一个要事后复述、且
    ``save_chapter_content`` 不会跟着重算的派生字段——用户手改一段正文，它就
    开始描述一个不存在的版本。跨章连续性改由「上一章蓝图 + 上一章结尾原文」
    承担，前者给弧线，后者是逐字真值。
    """

    __tablename__ = "chapters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    # 归属可改（章能换幕）。NOT NULL：一章没有幕就没有位置，全局章号
    # = 前序各幕章数之和 + 幕内序号，这个推导要求父子归属必须存在。
    act_id = Column(UUID(as_uuid=True), ForeignKey("acts.id", ondelete="CASCADE"), nullable=False, index=True)

    title = Column(String(200), nullable=False, default="新章")    # 章名
    goal = Column(Text, default="")                               # 本章要写什么（动笔前的计划）
    # 正文。延迟加载是结构性保证而非自觉：row_to_dict 按 mapper 检查
    # deferred 标记并跳过该列，所以列出章节的查询永远拖不上正文。
    #
    # raiseload=True 是配套的"失败要说人话"：在 async 会话里读一个没被
    # undefer 的延迟列会抛 MissingGreenlet（greenlet_spawn has not been
    # called），那句话完全看不出问题在哪——连写这个模型的人都踩过两次。
    # 加上 raiseload 之后，同样的错误会直接说
    # "'Chapter.content' is not available due to raiseload=True"，
    # 修法只有一句：用 with_chapter_content(...) 包住那个查询。
    content = deferred(Column(Text, default=""), raiseload=True)
    status = Column(String(20), nullable=False, default="draft")   # 这一章算不算过
    sort_order = Column(Integer, nullable=False, default=0)        # 章序（幕内）

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'revising', 'final')", name="ck_chapters_status"),
    )


# ============================================================
# 侧挂记录（不参与顺序，挂在项目上）
# ============================================================

class Character(StoryBase):
    __tablename__ = "characters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)      # 姓名
    role = Column(String(30), default="supporting")  # 定位 main/supporting/minor
    personality = Column(Text, default="")          # 性格（他是谁，静态）
    appearance = Column(Text, default="")           # 外貌
    background = Column(Text, default="")           # 背景
    motivation = Column(Text, default="")           # 他想要什么（当下）
    arc = Column(Text, default="")                  # 他从哪到哪（设计意图，起点→终点）
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    # 定位是一个枚举（主角 / 配角 / 次要）。约束写进模型，新建库由 create_all
    # 建出同一条约束，老库由迁移 0021 补——这样"新建的库"和"迁移过来的库"
    # 不会长出两套不同的 schema。
    __table_args__ = (
        CheckConstraint("role IN ('main', 'supporting', 'minor')", name="ck_characters_role"),
    )


class CharacterRelation(StoryBase):
    """一条记录，不是一张图。

    三个强度值（trust / threat / attraction，0-100）已删除：它们是伪精度，
    而且关系强度随剧情变化属于动态——静态记录里放动态值必然过时。
    关系的**事实性变化**写进这条记录的 ``description``（来龙去脉）；
    需要往后传的活线另建一条 ``Setup``。
    """

    __tablename__ = "character_relations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id = Column(UUID(as_uuid=True), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(UUID(as_uuid=True), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True)
    rel_type = Column(String(30), default="关联")   # 关系类型
    label = Column(String(100), default="")         # 关系标签
    description = Column(Text, default="")          # 说明
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class Setup(StoryBase):
    """伏笔：一对配对引用（埋在哪、在哪收），不是一条线。

    删除一章时的语义：
    - 删掉「埋在哪一章」那一章 → 这条伏笔不再埋在 any 地方，随章删除；
    - 删掉「打算在哪一章收」那一章 → 只是计划没了，伏笔本身还在，
      所以置空而不是删除。
    """

    __tablename__ = "setups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    note = Column(Text, default="")                                                          # 是什么
    planted_in = Column(UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True)   # 埋在哪一章
    pay_off_in = Column(UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True)    # 打算在哪一章收（可空）
    status = Column(String(20), nullable=False, default="open")                               # 收没收
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("status IN ('open', 'paid', 'dropped')", name="ck_setups_status"),
    )
