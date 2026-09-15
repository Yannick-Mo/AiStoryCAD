"""normalize characters.role into the main/supporting/minor vocabulary

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-12

为什么需要这一步
----------------
规格把人物的「定位」定成一个枚举：``main``（主角）/ ``supporting``（配角）/
``minor``（次要）。但历史数据里的 role 是创作时随手写的自由文本——
实测开发库里 6 个人物全是 ``protagonist`` / ``antagonist`` / ``supporting``，
**没有一个 main**。后果是前端的角色定位下拉框对所有人都显示为空，
而"定位"这个字段也就失去了它唯一的作用（一眼看出谁是主角）。

这里做两件事：把旧值归一化到这个词汇表，然后加上 CHECK 约束。
约束是"不变式由架构保证"的落地——写错值会当场报错，而不是悄悄存进去
一堆没人认得的字符串。同一约束也写进了 ``Character`` 模型，所以新建的库
由 create_all 直接建出它，两条路径不会分叉。

映射是判断，不是事实：``antagonist``（反派）按**戏份**归到 ``main``——
role 表达的是份量，不是立场。认不出来的值一律 ``supporting``。
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NORMALIZE = (
    # 主角级：protagonist / 反派 / 各种"主要"
    "UPDATE characters SET role = 'main' WHERE lower(coalesce(role, '')) IN "
    "('protagonist', 'antagonist', 'main', 'primary', 'lead', 'hero', 'villain', '主角', '主要', '反派')",
    # 次要级
    "UPDATE characters SET role = 'minor' WHERE lower(coalesce(role, '')) IN "
    "('minor', 'cameo', 'extra', 'background', '次要', '龙套', '路人')",
    # 其余（含空值）统一成配角——这一条必须是最后执行的那条
    "UPDATE characters SET role = 'supporting' WHERE role IS NULL OR role = '' "
    "OR lower(role) NOT IN ('main', 'supporting', 'minor')",
)

_ADD_CHECK = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_characters_role') THEN
        ALTER TABLE characters ADD CONSTRAINT ck_characters_role
            CHECK (role IN ('main', 'supporting', 'minor'));
    END IF;
END $$;
"""


def upgrade() -> None:
    for statement in _NORMALIZE:
        op.execute(statement)
    op.execute(_ADD_CHECK)


def downgrade() -> None:
    # 归一化是有损的：protagonist 与 antagonist 都被折成了 main，无法还原。
    op.execute("ALTER TABLE characters DROP CONSTRAINT IF EXISTS ck_characters_role")
