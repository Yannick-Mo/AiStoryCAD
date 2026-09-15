import uuid
from datetime import datetime
from typing import Any
from sqlalchemy import inspect as sa_inspect


def _serialize_value(val: Any) -> Any:
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, datetime):
        return val.isoformat()
    return val


# 延迟加载列的名字（目前只有 chapters.content），按 mapper 判定并缓存。
#
# 这是"列出章节的查询不得拖上正文"的结构性保证：row_to_dict 原本遍历
# __table__.columns 并 getattr 每一列，而 getattr 一个 deferred 列会触发
# 单行懒加载——于是每一次章节列表查询都会退化成 N+1 次正文读取。
# 改由 mapper 决定要跳过哪些列，将来再加延迟列也自动生效。
_DEFERRED_COLUMNS: dict[type, frozenset[str]] = {}


def deferred_columns(obj: Any) -> frozenset[str]:
    """该实例所属 mapper 上被标记为 deferred 的列名。"""
    cls = type(obj)
    cached = _DEFERRED_COLUMNS.get(cls)
    if cached is None:
        try:
            mapper = sa_inspect(obj).mapper
            cached = frozenset(
                attr.key for attr in mapper.column_attrs
                if getattr(attr, "deferred", False)
            )
        except Exception:
            cached = frozenset()
        _DEFERRED_COLUMNS[cls] = cached
    return cached


def row_to_dict(obj: Any) -> dict:
    if obj is None:
        return None
    try:
        if hasattr(obj, '__table__'):
            skip = deferred_columns(obj)
            return {
                c.name: _serialize_value(getattr(obj, c.name))
                for c in obj.__table__.columns
                if c.name not in skip
            }
        return dict(obj)
    except Exception:
        return dict(obj)
