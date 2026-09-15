"""merge the scene layer into chapters; drop edges, versions and project configs

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-11

背景（重要，先读这段）
----------------------
这个库从来没有跑过 alembic：它是被 ``app/database.py:init_db()`` 里的
``Base.metadata.create_all`` 引导建表的，所以 ``alembic_version`` 表并不存在，
0001-0019 的实际效果等于"当前模型长什么样，库里就是什么样"。

``create_all`` 只会**创建缺失的表**，永远不会 ALTER 已存在的表。因此
"把场景层合并进章"这种改列的操作**必须**走迁移，光改模型是没用的。

本迁移因此写成**自包含 + 幂等**：

* 所有 ADD / DROP 都带 ``IF (NOT) EXISTS`` 守卫，重复执行安全；
* 它自己处理 0018 / 0019 本该处理的事（清掉 themes / chapter_rhythms /
  consistency_* 等僵尸表、把幕与章重新连续编号），所以无论前面那些
  revision 是否真的跑过，从这里开始都是干净的。

数据搬运顺序不可颠倒：**先把场正文拼成章正文，再删 scenes**。
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ── 1. 新列 ─────────────────────────────────────────────────
_ADD_COLUMNS = (
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS ending text DEFAULT ''",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS spine text DEFAULT ''",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS total_words integer DEFAULT 100000",
    "ALTER TABLE acts ADD COLUMN IF NOT EXISTS purpose text DEFAULT ''",
    "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS result text DEFAULT ''",
    "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS content text DEFAULT ''",
    "ALTER TABLE characters ADD COLUMN IF NOT EXISTS arc text DEFAULT ''",
    "ALTER TABLE character_relations ADD COLUMN IF NOT EXISTS updated_at timestamptz",
    "UPDATE character_relations SET updated_at = created_at WHERE updated_at IS NULL",
)

# 模型里这些列没有服务端默认值（只有 Python 侧 default），
# 所以补完历史行之后把服务端默认值去掉，免得两边漂移。
_DROP_SERVER_DEFAULTS = (
    "ALTER TABLE projects ALTER COLUMN ending DROP DEFAULT",
    "ALTER TABLE projects ALTER COLUMN spine DROP DEFAULT",
    "ALTER TABLE projects ALTER COLUMN total_words DROP DEFAULT",
    "ALTER TABLE acts ALTER COLUMN purpose DROP DEFAULT",
    "ALTER TABLE chapters ALTER COLUMN result DROP DEFAULT",
    "ALTER TABLE chapters ALTER COLUMN content DROP DEFAULT",
    "ALTER TABLE characters ALTER COLUMN arc DROP DEFAULT",
)

# ── 2. 伏笔表 ───────────────────────────────────────────────
_CREATE_SETUPS = (
    """
    CREATE TABLE IF NOT EXISTS setups (
        id uuid PRIMARY KEY,
        project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        note text DEFAULT '',
        planted_in uuid NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
        pay_off_in uuid REFERENCES chapters(id) ON DELETE SET NULL,
        status character varying(20) NOT NULL DEFAULT 'open',
        created_at timestamptz DEFAULT now(),
        updated_at timestamptz DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_setups_project_id ON setups (project_id)",
    "CREATE INDEX IF NOT EXISTS ix_setups_planted_in ON setups (planted_in)",
    "CREATE INDEX IF NOT EXISTS ix_setups_pay_off_in ON setups (pay_off_in)",
)

# ── 3. 数据搬运 ─────────────────────────────────────────────
# 旧的 foreshadow 连线在新模型里就是一条伏笔记录：
# source_id = 埋设章节，target_id = 回收章节，label = 伏笔内容。
_FORESHADOW_EDGES_INTO_SETUPS = r"""
DO $$
BEGIN
    IF to_regclass('chapter_edges') IS NOT NULL THEN
        INSERT INTO setups (id, project_id, note, planted_in, pay_off_in, status, created_at, updated_at)
        SELECT e.id, e.project_id,
               COALESCE(NULLIF(e.label, ''), '未命名伏笔'),
               e.source_id, e.target_id, 'open', e.created_at, e.created_at
        FROM chapter_edges e
        WHERE e.edge_type = 'foreshadow' AND e.source_id <> e.target_id
        ON CONFLICT (id) DO NOTHING;
    END IF;
END $$;
"""

# 场正文按"场内序号"拼成章正文。空场不参与拼接，否则会拼出一串空行。
_SCENE_BODIES_INTO_CHAPTERS = r"""
DO $$
BEGIN
    IF to_regclass('scene_contents') IS NOT NULL THEN
        UPDATE chapters c
        SET content = sub.body
        FROM (
            SELECT s.chapter_id,
                   string_agg(sc.content, E'\n\n' ORDER BY s.sort_order, s.created_at, s.id) AS body
            FROM scenes s
            JOIN scene_contents sc ON sc.scene_id = s.id
            WHERE COALESCE(sc.content, '') <> ''
            GROUP BY s.chapter_id
        ) sub
        WHERE c.id = sub.chapter_id;
    END IF;
END $$;
"""

_TOTAL_WORDS_INTO_PROJECTS = r"""
DO $$
BEGIN
    IF to_regclass('project_configs') IS NOT NULL THEN
        UPDATE projects p
        SET total_words = cfg.total_words
        FROM project_configs cfg
        WHERE cfg.project_id = p.id AND cfg.total_words IS NOT NULL;
    END IF;
END $$;
"""

# ── 4. 顺序不变式：归属必须存在，序号必须连续 ───────────────
_RENUMBER_ACTS = """
UPDATE acts a SET sort_order = t.rn
FROM (
    SELECT id, row_number() OVER (
        PARTITION BY project_id ORDER BY sort_order, created_at, id
    ) AS rn FROM acts
) t
WHERE a.id = t.id AND a.sort_order IS DISTINCT FROM t.rn
"""

# 章没有幕就没有位置（全局章号 = 前序各幕章数之和 + 幕内序号），
# 所以 act_id 要变成 NOT NULL。先给没有幕的项目补一幕。
_DEFAULT_ACT_FOR_ACTLESS_PROJECTS = """
INSERT INTO acts (id, project_id, name, purpose, sort_order, color, created_at, updated_at)
SELECT gen_random_uuid(), p.id, '第一幕', '', 1, '#8b5cf6', now(), now()
FROM projects p
WHERE NOT EXISTS (SELECT 1 FROM acts a WHERE a.project_id = p.id)
"""

_ADOPT_ORPHAN_CHAPTERS = """
UPDATE chapters c
SET act_id = (
    SELECT a.id FROM acts a WHERE a.project_id = c.project_id
    ORDER BY a.sort_order DESC, a.created_at DESC, a.id DESC
    LIMIT 1
)
WHERE c.act_id IS NULL
"""

_RENUMBER_CHAPTERS = """
UPDATE chapters c SET sort_order = t.rn
FROM (
    SELECT id, row_number() OVER (
        PARTITION BY project_id, act_id ORDER BY sort_order, created_at, id
    ) AS rn FROM chapters
) t
WHERE c.id = t.id AND c.sort_order IS DISTINCT FROM t.rn
"""

_NORMALIZE_STATUS = (
    "UPDATE chapters SET status = 'draft' "
    "WHERE status IS NULL OR status NOT IN ('draft', 'revising', 'final')",
)

_SET_NOT_NULL = (
    "ALTER TABLE chapters ALTER COLUMN act_id SET NOT NULL",
    "ALTER TABLE chapters ALTER COLUMN status SET DEFAULT 'draft'",
    "ALTER TABLE chapters ALTER COLUMN status SET NOT NULL",
)

_ADD_CHECKS = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_chapters_status') THEN
        ALTER TABLE chapters ADD CONSTRAINT ck_chapters_status
            CHECK (status IN ('draft', 'revising', 'final'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_setups_status') THEN
        ALTER TABLE setups ADD CONSTRAINT ck_setups_status
            CHECK (status IN ('open', 'paid', 'dropped'));
    END IF;
END $$;
"""

# ── 5. 删列 ─────────────────────────────────────────────────
_DROP_COLUMNS = (
    "ALTER TABLE chapters DROP COLUMN IF EXISTS scene_count",
    "ALTER TABLE chapters DROP COLUMN IF EXISTS total_words",
    "ALTER TABLE projects DROP COLUMN IF EXISTS workflow_stage",
    "ALTER TABLE character_relations DROP COLUMN IF EXISTS trust",
    "ALTER TABLE character_relations DROP COLUMN IF EXISTS threat",
    "ALTER TABLE character_relations DROP COLUMN IF EXISTS attraction",
)

# ── 6. 删表 ─────────────────────────────────────────────────
# consistency_* / conflict_candidates 是"一致性检查引擎"的遗留表：代码早已
# 删除（0013 / 0015 引入），表却因为 create_all 从不删表而留了下来，且全部
# 是 0 行。其中两张还有指向 scenes 的外键，不先删就删不掉 scenes。
# themes / chapter_rhythms / theme_chapters 同理（0018 本该删掉）。
_DROP_TABLES = (
    "DROP TABLE IF EXISTS consistency_fact_queue",
    "DROP TABLE IF EXISTS consistency_facts",
    "DROP TABLE IF EXISTS conflict_candidates",
    "DROP TABLE IF EXISTS consistency_time_cache",
    "DROP TABLE IF EXISTS consistency_logs",
    "DROP TABLE IF EXISTS consistency_reports",
    "DROP TABLE IF EXISTS chapter_edges",
    "DROP TABLE IF EXISTS scene_contents",
    "DROP TABLE IF EXISTS scenes",
    "DROP TABLE IF EXISTS theme_chapters",
    "DROP TABLE IF EXISTS chapter_rhythms",
    "DROP TABLE IF EXISTS themes",
    "DROP TABLE IF EXISTS project_versions",
    "DROP TABLE IF EXISTS project_configs",
)


def upgrade() -> None:
    for statement in _ADD_COLUMNS:
        op.execute(statement)

    for statement in _CREATE_SETUPS:
        op.execute(statement)

    op.execute(_FORESHADOW_EDGES_INTO_SETUPS)
    op.execute(_SCENE_BODIES_INTO_CHAPTERS)
    op.execute(_TOTAL_WORDS_INTO_PROJECTS)

    op.execute(_RENUMBER_ACTS)
    op.execute(_DEFAULT_ACT_FOR_ACTLESS_PROJECTS)
    op.execute(_ADOPT_ORPHAN_CHAPTERS)
    op.execute(_RENUMBER_CHAPTERS)

    for statement in _NORMALIZE_STATUS:
        op.execute(statement)
    for statement in _SET_NOT_NULL:
        op.execute(statement)
    op.execute(_ADD_CHECKS)

    for statement in _DROP_SERVER_DEFAULTS:
        op.execute(statement)
    for statement in _DROP_COLUMNS:
        op.execute(statement)
    for statement in _DROP_TABLES:
        op.execute(statement)


def downgrade() -> None:
    # 不可逆：场与章之间的边界信息（哪段正文属于哪一场）在合并时就被丢弃了，
    # 无法把章正文切回场正文。真要回退，只能从备份恢复。
    pass
