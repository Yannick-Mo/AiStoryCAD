"""Tests for context builder — LRU cache, _is_meaningful_query."""

import time
from app.agent.context import _LRUCache, _is_meaningful_query


class TestLRUCache:
    def test_get_set(self):
        cache = _LRUCache(ttl=30, maxsize=10)
        cache.set("key1", {"data": 1})
        assert cache.get("key1") == {"data": 1}

    def test_missing_key(self):
        cache = _LRUCache(ttl=30, maxsize=10)
        assert cache.get("nonexistent") is None

    def test_expiry(self):
        import time as time_module
        start = time_module.monotonic()
        cache = _LRUCache(ttl=1, maxsize=10)
        cache.set("key1", {"data": 1})
        cache._store["key1"] = (start - 2, cache._store["key1"][1])
        assert cache.get("key1") is None

    def test_lru_eviction(self):
        cache = _LRUCache(ttl=30, maxsize=2)
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        cache.get("a")
        cache.set("c", {"v": 3})
        assert cache.get("b") is None
        assert cache.get("a") == {"v": 1}
        assert cache.get("c") == {"v": 3}

    def test_move_to_end_on_get(self):
        cache = _LRUCache(ttl=30, maxsize=2)
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        cache.get("a")
        cache.set("c", {"v": 3})
        assert cache.get("a") == {"v": 1}
        assert cache.get("b") is None

    def test_overwrite(self):
        cache = _LRUCache(ttl=30, maxsize=10)
        cache.set("key", {"v": 1})
        cache.set("key", {"v": 2})
        assert cache.get("key") == {"v": 2}


class TestIsMeaningfulQuery:
    def test_short_query_not_meaningful(self):
        assert not _is_meaningful_query("hi")

    def test_greetings_not_meaningful(self):
        assert not _is_meaningful_query("你好")
        assert not _is_meaningful_query("hello")
        assert not _is_meaningful_query("hey")

    def test_short_acknowledgments_not_meaningful(self):
        assert not _is_meaningful_query("嗯")
        assert not _is_meaningful_query("好的")
        assert not _is_meaningful_query("ok")

    def test_meaningful_query(self):
        assert _is_meaningful_query("请帮我分析这个角色的性格发展")
        assert _is_meaningful_query("What is the protagonist's motivation?")

    def test_greeting_with_punctuation(self):
        assert not _is_meaningful_query("你好！")
        assert not _is_meaningful_query("hello?")


class TestDeletePrefix:
    def test_deletes_matching_keys_only(self):
        cache = _LRUCache(ttl=30, maxsize=10)
        cache.set("ctx_cache:p1:full:", {"a": 1})
        cache.set("ctx_cache:p1:summary:minimal", {"b": 2})
        cache.set("ctx_cache:p2:full:", {"c": 3})
        cache.delete_prefix("ctx_cache:p1:")
        assert cache.get("ctx_cache:p1:full:") is None
        assert cache.get("ctx_cache:p1:summary:minimal") is None
        assert cache.get("ctx_cache:p2:full:") == {"c": 3}

    def test_no_matches_is_noop(self):
        cache = _LRUCache(ttl=30, maxsize=10)
        cache.set("ctx_cache:p9:full:", {"a": 1})
        cache.delete_prefix("ctx_cache:nope:")
        assert cache.get("ctx_cache:p9:full:") == {"a": 1}


class TestInvalidateProject:
    """H3: after a write, the shared 300s context cache must be dropped so read
    tools in the same turn serve fresh data."""

    def test_invalidate_clears_this_project_only(self):
        from app.agent.context import ContextBuilder, _CONTEXT_CACHE
        import uuid

        pid = uuid.uuid4()
        other = uuid.uuid4()

        _CONTEXT_CACHE.set(f"ctx_cache:{pid}:full:", {"x": 1})
        _CONTEXT_CACHE.set(f"ctx_cache:{pid}:summary:framework", {"y": 2})
        _CONTEXT_CACHE.set(f"ctx_cache:{other}:full:", {"z": 3})

        ContextBuilder.invalidate_project(pid)

        assert _CONTEXT_CACHE.get(f"ctx_cache:{pid}:full:") is None
        assert _CONTEXT_CACHE.get(f"ctx_cache:{pid}:summary:framework") is None
        assert _CONTEXT_CACHE.get(f"ctx_cache:{other}:full:") == {"z": 3}


class TestRagFetchedOnDemandOnly:
    """RAG knowledge must be fetched on demand (final response pass), never
    baked into build_summary's shared per-project cache (it is query-dependent
    and would pollute other sessions' snapshots)."""

    async def test_get_rag_context_fetches_fresh(self):
        from unittest.mock import AsyncMock

        from app.agent.context import ContextBuilder
        import uuid

        builder = ContextBuilder(AsyncMock())
        pid = uuid.uuid4()

        builder._get_project = AsyncMock(return_value=type("P", (), {"genre": "fantasy"})())
        builder._get_rag_context_if_meaningful = AsyncMock(return_value="KNOWLEDGE")

        out = await builder.get_rag_context(pid, query_hint="分析角色")
        assert out == "KNOWLEDGE"
        builder._get_rag_context_if_meaningful.assert_awaited_once()

    async def test_build_summary_never_contains_rag(self):
        from unittest.mock import AsyncMock

        from app.agent.context import ContextBuilder
        import uuid

        builder = ContextBuilder(AsyncMock())
        pid = uuid.uuid4()
        cached = {"project": {"title": "T", "genre": "", "global_settings": ""},
                  "acts": [], "characters": [], "relations": [], "edges": []}
        builder._cache_get = AsyncMock(return_value=cached)
        builder._get_rag_context_if_meaningful = AsyncMock(return_value="KNOWLEDGE")
        out = await builder.build_summary(pid, depth="framework")
        assert "rag_context" not in out
        builder._get_rag_context_if_meaningful.assert_not_awaited()


class TestTrimContextPreservesTier0InsertionOrder:
    """M14: sorting by (tier, label) reversed the deliberate precedence within a
    tier — e.g. cowriter_persona sorted before the base persona."""

    def test_base_persona_precedes_cowriter_persona(self):
        from app.agent.response_builder import _ContextSection, trim_context

        sections = [
            _ContextSection(tier=0, label="persona", text="BASE_PERSONA"),
            _ContextSection(tier=0, label="mode", text="MODE_DECL"),
            _ContextSection(tier=0, label="project_title", text="TITLE"),
            _ContextSection(tier=0, label="cowriter_persona", text="COWRITER_PERSONA"),
        ]
        out = trim_context(sections)
        assert out.index("BASE_PERSONA") < out.index("COWRITER_PERSONA"), (
            "alphabetical tiebreak reversed tier-0 precedence"
        )
        assert out.index("MODE_DECL") < out.index("COWRITER_PERSONA")
