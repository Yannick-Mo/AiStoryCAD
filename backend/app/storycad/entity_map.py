"""编辑器/API 的实体类型名 → 模型映射。

``scenes`` 与 ``edges`` 已随场景层和连线层一起删除；新增 ``setups``（伏笔）。
类型名与前端 URL 里用的复数形式保持一致。
"""

from app.storycad.models import Act, Chapter, Character, CharacterRelation, Setup

ENTITY_MAP = {
    "acts": Act,
    "chapters": Chapter,
    "characters": Character,
    "character_relations": CharacterRelation,
    "setups": Setup,
}
