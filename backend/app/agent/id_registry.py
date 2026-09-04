"""Entity ID Registry — bounded, per-turn-rebuilt index of entity IDs for tool chaining.

Replaces the old "keep whole tool-result messages alive across compression"
strategy (context_compressor ``_ID_SOURCE_TOOLS`` / history_manager
``_LIST_TOOLS_FOR_ID``).  Instead of verbatim-preserving tool messages, an
ID↔label table is rebuilt each turn from the latest tool results and rendered
into the system prompt as a compact section.  The table survives compression
(because ``state.tool_results`` is never compressed) and, via ``seen``/version
bookkeeping, is persisted across user requests so IDs stay usable across turns.

Registry shape (JSON-serializable for Redis persistence)::

    {
      "scene": {
        "sc-xxxx-xxxx": {"label": "郊外咖啡馆", "seen": 17},
        ...
      },
      ...
    }

Design doc: ``docs/ID寄存表设计文档_v2.md``
"""

from __future__ import annotations

import json
import re
from typing import Any

# ── Tuning constants ─────────────────────────────────────────────────────

WINDOW_SIZE = 20       # latest tool_results considered per request
MAX_ENTRIES = 200      # hard cap on registry size
RENDER_MAX_CHARS = 6000
STALE_WINDOW = 16       # a persisted entry expires after N unconfirmed versions

# ── Entity type vocabulary ───────────────────────────────────────────────

_ENTITY_TYPES: tuple[str, ...] = (
    "act", "chapter", "scene", "character", "relation", "edge",
)

_TYPE_CN: dict[str, str] = {
    "act": "幕",
    "chapter": "章",
    "scene": "场景",
    "character": "角色",
    "relation": "关系",
    "edge": "连线",
}

# Container key in a tool-result JSON → entity type of the contained items.
_CONTAINER_TYPES: dict[str, str] = {
    "acts": "act",
    "chapters": "chapter",
    "scenes": "scene",
    "characters": "character",
    "relations": "relation",
    "edges": "edge",
}

# Identity field to use as the display label for each entity type.
_LABEL_FIELDS: dict[str, tuple[str, ...]] = {
    "act": ("name",),
    "chapter": ("title",),
    "scene": ("title",),
    "character": ("name",),
    "relation": ("character_name", "target_name"),
    "edge": ("source_title", "target_title"),
}

# Tools whose *whole* result is a flat dict for a single entity (container
# recursion can't see these — the entity is the top-level dict, not inside a
# "characters"/"scenes" key).  Map tool → entity type.
_FLAT_ENTITY_TOOL: dict[str, str] = {
    "read_character": "character",
    "read_scene": "scene",
    "read_chapter": "chapter",
    "create_character": "character",
    "create_scene": "scene",
    "create_chapter": "chapter",
    "create_act": "act",
    "create_edge": "edge",
    "create_relation": "relation",
    "update_character": "character",
    "update_scene": "scene",
    "update_chapter": "chapter",
    "update_act": "act",
    "update_edge": "edge",
    "update_relation": "relation",
}

# Unfiltered list_* results are the authoritative full set for their type —
# they REPLACE the whole type (pruning deleted entities deterministically).
# Tool → entity type.
_SNAPSHOT_TOOLS: dict[str, str] = {
    "list_characters": "character",
    "list_relations": "relation",
    "list_edges": "edge",
}

# Params that, if present, make a list_* result a *filtered* (non-authoritative)
# subset.
_FILTER_PARAMS: dict[str, set[str]] = {
    "list_character_relations": {"character_id"},
    "list_characters": set(),
    "list_edges": set(),
}

# Delete tools → (entity_type, data-key) pairs.  Keys may hold a single id
# string or a list of ids.
_DELETE_EVICTION: dict[str, list[tuple[str, str]]] = {
    "delete_act": [
        ("act", "deleted_act_id"),
        ("chapter", "deleted_chapter_ids"),
        ("scene", "deleted_scene_ids"),
    ],
    "delete_chapter": [
        ("chapter", "deleted_chapter_id"),
        ("scene", "deleted_scene_ids"),
    ],
    "delete_scene": [("scene", "deleted_scene_id")],
    "delete_character": [
        ("character", "character_id"),
        ("relation", "deleted_relation_ids"),
    ],
    "delete_relation": [("relation", "relation_id")],
    "delete_edge": [("edge", "deleted_edge_id")],
}

# Markers appended by streaming_executor._summarise_tool_output when a JSON
# result was hard-truncated at the tail.  A truncated list_* is NOT a valid
# authoritative snapshot (see _is_truncated).
_TRUNCATION_MARKERS: tuple[str, ...] = (
    "[truncated,",
    "(完整列表",
    "[中间省略",
)

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


# ── Low-level helpers ─────────────────────────────────────────────────────


def _parse_data(data: Any) -> Any | None:
    """Parse a tool result ``data`` into a Python object.

    ``data`` is always a string after ``_summarise_tool_output`` (unless it
    was forged by an interceptor).  Returns ``None`` on parse failure so the
    caller can skip the result silently.
    """
    if isinstance(data, (dict, list)):
        return data
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _is_truncated(text: str) -> bool:
    return any(m in text for m in _TRUNCATION_MARKERS)


def _label_quality(label: str) -> int:
    """Score a label: human-meaningful text > degraded uuid/“?” strings."""
    if not label:
        return 0
    stripped = _UUID_RE.sub("", label).replace("?", "")
    return len(stripped)


def _label_is_better(new: str, old: str) -> bool:
    return _label_quality(new) > _label_quality(old)


def _compose_label(entity_type: str, item: dict) -> str:
    """Build a display label for *item* of *entity_type*.

    Relations/edges get ``A → B`` labels when names are available; otherwise
    they degrade to ``关系: <id>`` / ``连线: <id>``.
    """
    if entity_type in ("relation", "edge"):
        if entity_type == "relation":
            a = item.get("character_name") or "?"
            b = item.get("target_name") or "?"
            rel = item.get("label") or item.get("rel_type") or "关系"
        else:
            a = item.get("source_title") or "?"
            b = item.get("target_title") or "?"
            rel = item.get("label") or item.get("edge_type") or "连线"
        if "?" not in a and "?" not in b:
            return f"{rel}: {str(a)[:40]} → {str(b)[:40]}"
        eid = item.get("id") or item.get(f"{entity_type}_id")
        return f"{rel}: {eid or '?'}"
    for field in _LABEL_FIELDS.get(entity_type, ()):
        val = item.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()[:60]
    return ""


def _collect(
    reg: dict[str, dict[str, dict]],
    entity_type: str,
    entity_id: Any,
    label: str,
    seen: int,
) -> None:
    """Upsert one entity into the registry.

    Priority rules:
      * A fresher source (higher ``seen``) always wins — so a per-request
        window entry overrides a persisted entry even on an equal label.
      * Within the same ``seen``, the better label wins; ties keep the
        first-seen entry (callers iterate newest-first).
    """
    if not entity_type or not entity_id or not label:
        return
    eid = str(entity_id)
    bucket = reg.setdefault(entity_type, {})
    cur = bucket.get(eid)
    if cur is None:
        bucket[eid] = {"label": label, "seen": seen}
        return
    cur_seen = cur.get("seen", 0)
    if seen > cur_seen:
        bucket[eid] = {"label": label, "seen": seen}
    elif seen == cur_seen:
        if _label_is_better(label, cur.get("label", "")):
            bucket[eid] = {"label": label, "seen": seen}


def _walk(
    node: Any,
    reg: dict[str, dict[str, dict]],
    seen: int,
) -> None:
    """Recursively collect entities found under known container keys."""
    if isinstance(node, dict):
        for key, value in node.items():
            etype = _CONTAINER_TYPES.get(key)
            if etype is not None and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _collect(
                            reg, etype, item.get("id"),
                            _compose_label(etype, item), seen,
                        )
                        _walk(item, reg, seen)
            elif isinstance(value, (dict, list)):
                _walk(value, reg, seen)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, dict):
                _walk(item, reg, seen)


def _extract_flat_entity(
    tool_name: str,
    data: Any,
    reg: dict[str, dict[str, dict]],
    seen: int,
) -> None:
    """Extract a single flat entity dict (read_*/create_*/update_* results).

    These tools return the entity as the top-level dict, which container-key
    recursion never sees.  ID may be under ``id`` or ``<entity>_id``.
    """
    if not isinstance(data, dict):
        return
    etype = _FLAT_ENTITY_TOOL.get(tool_name)
    if not etype:
        return
    eid = data.get("id") or data.get(f"{etype}_id")
    if not eid:
        return
    _collect(reg, etype, eid, _compose_label(etype, data), seen)


def _has_filter(tool_name: str, params: dict) -> bool:
    filters = _FILTER_PARAMS.get(tool_name, set())
    for key in filters:
        if params.get(key):
            return True
    return False


def _crop(reg: dict[str, dict[str, dict]], max_entries: int = MAX_ENTRIES) -> None:
    """Drop oldest entries (by ``seen``) to keep the registry bounded."""
    flat = []
    for etype, entries in reg.items():
        for eid, meta in entries.items():
            flat.append((etype, eid, meta.get("seen", 0)))
    if len(flat) <= max_entries:
        return
    flat.sort(key=lambda t: t[2], reverse=True)
    keep = set((e, i) for e, i, _ in flat[:max_entries])
    for etype in list(reg.keys()):
        reg[etype] = {
            eid: meta for eid, meta in reg[etype].items()
            if (etype, eid) in keep
        }
        if not reg[etype]:
            del reg[etype]


# ── Public API ───────────────────────────────────────────────────────────


def build_id_registry(
    tool_results: list[dict],
    persisted: dict | None = None,
    version: int = 1,
    stale_window: int = STALE_WINDOW,
    window_size: int = WINDOW_SIZE,
) -> dict[str, dict[str, dict]]:
    """Build the merged registry from a per-request tool-result window plus
    the persisted registry carried across requests.

    - Persisted entries older than ``version - stale_window`` are pruned.
    - The newest **unfiltered** list_* result per type replaces that type
      (deterministic pruning of deleted entities).  Truncated or filtered
      list results only upsert.
    - Delete tool results evict their ids (and cascade child ids).
    """
    reg: dict[str, dict[str, dict]] = {}

    # 1. Load persisted entries (prune stale by seen)
    cutoff = version - stale_window
    for etype, entries in (persisted or {}).items():
        if not isinstance(entries, dict):
            continue
        for eid, meta in entries.items():
            if not isinstance(meta, dict):
                continue
            try:
                seen = int(meta.get("seen", 0) or 0)
            except (TypeError, ValueError):
                continue
            if seen < cutoff:
                continue
            label = str(meta.get("label", "") or "")
            if not label:
                continue
            reg.setdefault(etype, {})[str(eid)] = {"label": label, "seen": seen}

    # 2. Pass A — snapshots + generic collection, newest first.
    #    A type is replaced ONLY by its NEWEST snapshot result, and only when
    #    that result is unfiltered and untruncated (an authoritative full set).
    #    Older snapshots of the same type are ignored entirely (they carry
    #    stale data and must not resurrect entries the newest one removed).
    window = list(tool_results or [])[-window_size:]
    newest_snapshot: set[str] = set()
    for r in reversed(window):
        if not isinstance(r, dict) or not r.get("success"):
            continue
        tool_name = r.get("tool", "")
        data = r.get("data")
        if not tool_name or not data:
            continue
        parsed = _parse_data(data)
        if parsed is None:
            continue

        if tool_name in _SNAPSHOT_TOOLS:
            etype = _SNAPSHOT_TOOLS[tool_name]
            if etype in newest_snapshot:
                continue
            newest_snapshot.add(etype)
            params = r.get("params")
            filtered = isinstance(params, dict) and _has_filter(tool_name, params)
            truncated = isinstance(data, str) and _is_truncated(data)
            if not filtered and not truncated:
                reg[etype] = {}
            _walk(parsed, reg, version)
        elif tool_name in _FLAT_ENTITY_TOOL:
            _extract_flat_entity(tool_name, parsed, reg, version)
            _walk(parsed, reg, version)
        else:
            _walk(parsed, reg, version)

    # 3. Pass B — delete eviction, newest first so the latest delete wins.
    for r in reversed(window):
        if not isinstance(r, dict) or not r.get("success"):
            continue
        specs = _DELETE_EVICTION.get(r.get("tool", ""))
        if not specs:
            continue
        parsed = _parse_data(r.get("data"))
        if not isinstance(parsed, dict):
            continue
        for etype, key in specs:
            raw = parsed.get(key)
            if raw is None:
                continue
            if isinstance(raw, list):
                for eid in raw:
                    if isinstance(eid, str):
                        reg.get(etype, {}).pop(eid, None)
            elif isinstance(raw, str):
                reg.get(etype, {}).pop(raw, None)

    # 4. Drop empty type buckets left by delete eviction
    for etype in list(reg.keys()):
        if not reg[etype]:
            del reg[etype]

    # 5. Bound total size
    _crop(reg, MAX_ENTRIES)
    return reg


def render_id_registry(
    reg: dict[str, dict[str, dict]],
    max_entries: int = MAX_ENTRIES,
    max_chars: int = RENDER_MAX_CHARS,
) -> str:
    """Render the registry as a compact system-prompt section.

    Returns ``""`` when the registry is empty (caller skips injection).
    """
    if not reg:
        return ""

    lines = ["# --- 已知实体 ID（工具调用可直接引用，无需先查询）---"]
    total = 0
    for etype in _ENTITY_TYPES:
        entries = reg.get(etype)
        if not entries:
            continue
        lines.append(f"## {_TYPE_CN.get(etype, etype)}（{len(entries)}）")
        for eid, meta in sorted(entries.items(), key=lambda kv: kv[1].get("label", "")):
            label = meta.get("label", "")
            lines.append(f"  {label} | {eid}")
            total += 1
            if total >= max_entries:
                break
        if total >= max_entries:
            break

    lines.append("（注：ID 可能已过期，若调用失败请重新调用获取工具取最新 ID——如 read_chapters / read_chapter_scenes / list_characters 等）")
    rendered = "\n".join(lines)
    return rendered[:max_chars]
