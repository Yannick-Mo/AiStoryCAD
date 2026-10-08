"""drop chapters.result — 「本章写成了什么」

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-13

为什么删掉
----------
``chapters.result`` 是「本章写成了什么」——按规格的原始设计，它是跨章连续性的
唯一载体：写手写完一章，编排器调 ``update_chapter`` 把这一章的实际结局补上，
下一章靠它接上。这个设计有两个致命问题：

**一、它是一个会静默腐烂的派生字段。** ``save_chapter_content`` 只改
``chapter.content``，不碰 ``result``，也没有任何东西会重算它。所以用户在编辑器里
手改一段正文之后，``result`` 就开始描述一个**不存在的版本**——而且没有任何症状。
更糟的是界面上它就紧挨着 ``goal``，是一个看起来该由用户自己维护的输入框：
产品实际上要求小说家兼任数据库的一致性机制，而没有人会记得回去改那句摘要。

**二、它和 ``goal`` 是同一件东西的两份副本。** ``goal`` 是动笔前的计划，``result``
是动笔后的复述——两者重叠，而且提示词自己的冲突取舍顺序里压根没有它：

    已写正文说明的事实 > 本章蓝图 > 人物行为逻辑 > 类型惯例

最高权威是**已写正文**。也就是说这个字段从来没被提示词当成权威，只被当成"方便
的一句摘要"。

替代方案
--------
连续性改由两样**逐字真值**承担，都在 ``ContextBuilder.build_for_writing`` 里：

- ``previous_chapter_goal`` —— 上一章的蓝图，给写手一条弧线；
- ``previous_chapter_tail`` —— 上一章结尾的原文（无蓝图时从 800 字放宽到
  2000 字），给起点与语感。用户改一个字它就跟着变。

计划会被偏离（提示词明确允许"蓝图之外可以长东西"），所以起点只认原文。

需要往后传、且不能靠读原文发现的活线，改建一条 ``Setup``——那是有生命周期
（open / paid / dropped）和 ``pay_off_in`` 的真实实体，不是散文块。

这条迁移是**有损的**：``result`` 里已有的内容无法还原成 ``Setup``（它不是结构化
数据）。downgrade 只能把列加回来，内容为空。
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE chapters DROP COLUMN IF EXISTS result")


def downgrade() -> None:
    # 有损：原内容已在 upgrade 里丢掉，只能加回一个空列。
    op.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS result TEXT DEFAULT ''")