from __future__ import annotations

import html as html_mod
import logging
import re
import time
from collections import OrderedDict
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.guard import check_web_content_safety
from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode
from app.config import settings

logger = logging.getLogger(__name__)

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()


def _cache_get(key: str) -> list[dict] | None:
    if key not in _cache:
        return None
    ts, data = _cache[key]
    if time.monotonic() - ts > settings.web_fetch_cache_ttl:
        del _cache[key]
        return None
    _cache.move_to_end(key)
    return data


def _cache_set(key: str, data: list[dict]):
    _cache[key] = (time.monotonic(), data)
    while len(_cache) > settings.web_fetch_cache_max:
        _cache.popitem(last=False)


# ── Helpers ────────────────────────────────────────────────────────────────────

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", re.UNICODE)


def _extract_cjk_chars(text: str) -> set[str]:
    return set(_CJK_RE.findall(text))


def _compute_relevance(query: str, snippet: str) -> float:
    query_lower = query.lower().strip()
    snippet_lower = snippet.lower().strip()
    cjk_query = _extract_cjk_chars(query_lower)
    cjk_snippet = _extract_cjk_chars(snippet_lower)
    if cjk_query:
        if not cjk_snippet:
            return 0.0
        return len(cjk_query & cjk_snippet) / len(cjk_query)
    query_words = set(query_lower.split())
    if not query_words:
        return 0.0
    snippet_words = set(snippet_lower.split())
    return len(query_words & snippet_words) / len(query_words)


def _domain_match(url: str, domains: list[str]) -> bool:
    host = urlparse(url).hostname or ""
    for d in domains:
        d = d.strip().lower()
        if d.startswith("."):
            if host.endswith(d) or host == d[1:]:
                return True
        elif host == d:
            return True
    return False


def _filter_results(
    raw: list[dict],
    query: str,
    max_results: int,
    allowed_domains: list[str] | None,
    blocked_domains: list[str] | None,
) -> list[dict]:
    seen_urls: set[str] = set()
    clean: list[dict] = []

    for r in raw:
        url = (r.get("url") or r.get("link") or "").strip()
        snippet = (r.get("content") or r.get("snippet") or r.get("description") or "").strip()

        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        if allowed_domains and not _domain_match(url, allowed_domains):
            continue
        if blocked_domains and _domain_match(url, blocked_domains):
            continue

        if len(snippet) < settings.search_min_snippet_len:
            continue

        relevance = _compute_relevance(query, snippet)
        clean.append({
            "title": (r.get("title") or "").strip(),
            "url": url,
            "snippet": snippet,
            "relevance": round(relevance, 2),
        })

    clean.sort(key=lambda x: x["relevance"], reverse=True)
    return clean[:max_results]


# ── SearXNG backend ────────────────────────────────────────────────────────────


async def _search_searxng(
    query: str,
    max_results: int,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict]:
    cache_key = f"sxng:{query}:{max_results}:{allowed_domains}:{blocked_domains}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = f"{settings.searxng_url.rstrip('/')}/search"
    params = {"q": query, "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        raw = data.get("results", [])
        results = _filter_results(raw, query, max_results, allowed_domains, blocked_domains)
        if results:
            _cache_set(cache_key, results)
            return results

        logger.info("SearXNG returned no results for query=%s, trying DDG fallback", query)
    except Exception as e:
        logger.warning("SearXNG search failed for query=%s: %s", query, e)

    return []


# ── DuckDuckGo fallback backend ────────────────────────────────────────────────


def _parse_ddg_lite_html(text: str) -> list[dict]:
    results = []
    current = None
    for row_match in re.finditer(r'<tr[^>]*>(.*?)</tr>', text, re.DOTALL | re.IGNORECASE):
        row = row_match.group(1)
        title_match = re.search(
            r'<a[^>]*class=["\']result-link["\'][^>]*>(.*?)</a>',
            row, re.DOTALL | re.IGNORECASE,
        )
        if title_match:
            if current and current.get("title"):
                results.append(current)
            title = html_mod.unescape(re.sub(r"<[^>]+>", "", title_match.group(1)).strip())
            url_match = re.search(r'href="(https?://[^"]+)"', row)
            url = html_mod.unescape(url_match.group(1)) if url_match else ""
            current = {"title": title, "url": url, "snippet": ""}
            continue
        if current:
            snippet_match = re.search(
                r'<td[^>]*class=["\']result-snippet["\'][^>]*>(.*?)</td>',
                row, re.DOTALL | re.IGNORECASE,
            )
            if snippet_match:
                current["snippet"] = html_mod.unescape(
                    re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()
                )
    if current and current.get("title"):
        results.append(current)
    return results


async def _search_duckduckgo(
    query: str,
    max_results: int,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict]:
    cache_key = f"ddg:{query}:{max_results}:{allowed_domains}:{blocked_domains}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": query},
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; StoryCAD/1.0)",
                    "Accept": "text/html",
                },
            )
            resp.raise_for_status()
        raw = _parse_ddg_lite_html(resp.text)
        if raw:
            results = _filter_results(raw, query, max_results, allowed_domains, blocked_domains)
            _cache_set(cache_key, results)
            return results
    except Exception as e:
        logger.warning("DDG fallback failed for query=%s: %s", query, e)

    return []


# ── Orchestrator ───────────────────────────────────────────────────────────────


async def _search(
    query: str,
    max_results: int,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict]:
    results = await _search_searxng(query, max_results, allowed_domains, blocked_domains)
    if results:
        return results
    if settings.search_enable_ddg_fallback:
        results = await _search_duckduckgo(query, max_results, allowed_domains, blocked_domains)
    return results


# ── Tool class ─────────────────────────────────────────────────────────────────


class WebSearchTool(BaseTool):
    meta = ToolMeta(
        name="web_search",
        description="搜索网络获取实时信息。适用于查询最新新闻、事实数据、写作参考资料等。返回URL后可调用 web_fetch 获取详情",
        concurrency=ConcurrencyMode.SAFE,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（支持中文）",
                    "maxLength": 500,
                },
                "max_results": {
                    "type": "integer",
                    "description": "返回结果数量 (1-10)，默认5",
                    "default": 5,
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "仅返回这些域名下的结果（如 [\"python.org\"]）",
                },
                "blocked_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "排除这些域名的结果",
                },
            },
            "required": ["query"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        query = kwargs.get("query", "").strip()
        if not query:
            return ToolResult(success=False, error="Query is required")

        max_results = max(1, min(kwargs.get("max_results", 5), settings.search_max_results))
        allowed = kwargs.get("allowed_domains")
        blocked = kwargs.get("blocked_domains")

        try:
            results = await _search(query, max_results, allowed, blocked)
            if not results:
                return ToolResult(
                    success=True,
                    data={"results": [], "message": "未找到相关结果，请尝试更换关键词或减少过滤条件"},
                )
            # 搜索结果(标题/摘要)是外部可控文本,注入主循环前先过 guard 的
            # 注入/危险内容检测;命中则丢弃该条。在缓存读取之后过滤,缓存不变。
            safe_results = []
            for r in results:
                text = f"{r.get('title', '')} {r.get('snippet', '')}"
                if check_web_content_safety(text):
                    logger.warning("web_search result filtered for query=%s url=%s", query, r.get("url", ""))
                    continue
                safe_results.append(r)
            if not safe_results:
                return ToolResult(
                    success=True,
                    data={"results": [], "message": "未找到相关结果，请尝试更换关键词或减少过滤条件"},
                )
            return ToolResult(success=True, data=safe_results)
        except Exception as e:
            logger.exception("web_search failed for query=%s", query)
            return ToolResult(success=False, error=str(e))
