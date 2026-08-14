from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from collections import OrderedDict
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools.base import BaseTool, ToolResult, ToolMeta, ConcurrencyMode
from app.config import settings

logger = logging.getLogger(__name__)

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()


def _cache_get(key: str) -> str | None:
    if key not in _cache:
        return None
    ts, data = _cache[key]
    if time.monotonic() - ts > settings.web_fetch_cache_ttl:
        del _cache[key]
        return None
    _cache.move_to_end(key)
    return data


def _cache_set(key: str, data: str):
    _cache[key] = (time.monotonic(), data)
    while len(_cache) > settings.web_fetch_cache_max:
        _cache.popitem(last=False)


# ── Preapproved domains (bypass content warning) ───────────────────────────────
_PREAPPROVED_DOMAINS: set[str] = {
    "wikipedia.org", "en.wikipedia.org", "zh.wikipedia.org",
    "python.org", "docs.python.org",
    "github.com",
    "stackoverflow.com",
    "stackexchange.com",
    "developer.mozilla.org", "mdn.dev",
    "react.dev", "nextjs.org", "vuejs.org",
    "docs.docker.com",
    "kubernetes.io",
    "redis.io",
    "postgresql.org",
    "nginx.org",
    "pypi.org",
    "npmjs.com",
    "nodejs.org",
    "typescriptlang.org",
    "fastapi.tiangolo.com",
    "sqlalchemy.org",
}

# ── HTML→text extraction ───────────────────────────────────────────────────────

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False
    logger.info("trafilatura not installed; web_fetch will use fallback HTML stripping")


def _extract_text(html: str, url: str) -> str:
    """Extract readable text from HTML, preferring trafilatura."""
    if HAS_TRAFILATURA:
        try:
            result = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                include_images=False,
                include_links=True,
                output_format="markdown",
                favor_precision=True,
            )
            if result and len(result) > 20:
                return result.strip()
        except Exception as e:
            logger.debug("trafilatura extraction failed for %s: %s", url, e)

    # Fallback: basic text extraction
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Content fetching ───────────────────────────────────────────────────────────


def _is_preapproved(url: str) -> bool:
    host = urlparse(url).hostname or ""
    for domain in _PREAPPROVED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _validate_url(url: str) -> str | None:
    if len(url) > 2000:
        return "URL too long (max 2000 characters)"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "Only http and https URLs are supported"
    if not parsed.hostname:
        return "Invalid URL: no hostname"
    if parsed.username or parsed.password:
        return "URL with credentials is not allowed"
    return None


def _is_blocked_address(ip_str: str) -> bool:
    """Reject private, loopback, link-local, reserved, multicast and unspecified
    addresses (SSRF guard — metadata endpoints and internal services live here)."""
    try:
        addr = ipaddress.ip_address(ip_str.split("%")[0])
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_reserved or addr.is_multicast or addr.is_unspecified:
        return True
    if addr.version == 6 and addr.ipv4_mapped is not None:
        return _is_blocked_address(str(addr.ipv4_mapped))
    return False


async def _resolve_and_check(hostname: str) -> str | None:
    """Resolve a hostname and reject it if any resolved address is blocked.

    IP literals (including IPv6 in brackets) are checked directly without DNS.
    """
    host = hostname.strip("[]")
    try:
        ipaddress.ip_address(host)
        return None if not _is_blocked_address(host) else "URL resolves to a private or reserved address"
    except ValueError:
        pass
    try:
        infos = await asyncio.get_event_loop().getaddrinfo(host, None, type=0)
    except Exception:
        return "Invalid URL: hostname could not be resolved"
    if not infos:
        return "Invalid URL: hostname could not be resolved"
    for info in infos:
        ip_str = info[4][0]
        if _is_blocked_address(ip_str):
            return "URL resolves to a private or reserved address"
    return None


async def _validate_url_async(url: str) -> str | None:
    """Full URL validation: syntax + DNS resolution + SSRF blocklist."""
    error = _validate_url(url)
    if error:
        return error
    parsed = urlparse(url)
    assert parsed.hostname is not None
    return await _resolve_and_check(parsed.hostname)


async def _fetch_url(url: str) -> tuple[str | None, str | None]:
    """Fetch a URL and return (text_content, error)."""
    cache_key = f"fetch:{url}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached, None

    redirects_left = 10
    current_url = url
    try:
        async with httpx.AsyncClient(
            timeout=settings.web_fetch_timeout,
            follow_redirects=False,
        ) as client:
            while True:
                error = await _validate_url_async(current_url)
                if error:
                    return None, error
                resp = await client.get(
                    current_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; StoryCAD/1.0; +https://storycad.app)",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "zh-CN,en;q=0.9",
                    },
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        break
                    redirects_left -= 1
                    if redirects_left < 0:
                        return None, "Too many redirects"
                    current_url = urljoin(current_url, location)
                    continue
                resp.raise_for_status()
                break

        content_type = resp.headers.get("content-type", "").lower()
        if "text/" not in content_type and "html" not in content_type:
            return None, f"Unsupported content type: {content_type}"

        text = _extract_text(resp.text, current_url)
        if not text or len(text) < 20:
            return None, "Page has no readable content"

        if len(text) > settings.web_fetch_max_chars:
            text = text[:settings.web_fetch_max_chars] + "\n\n[... content truncated ...]"

        _cache_set(cache_key, text)
        return text, None

    except httpx.TimeoutException:
        return None, f"Request timed out after {settings.web_fetch_timeout}s"
    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}"
    except Exception as e:
        return None, str(e)


# ── Tool class ─────────────────────────────────────────────────────────────────


class WebFetchTool(BaseTool):
    meta = ToolMeta(
        name="web_fetch",
        description="抓取指定 URL 的正文内容并转为纯文本/ Markdown。适用于阅读文章、文档、教程等在线内容。URL通常来自 web_search 的返回结果",
        concurrency=ConcurrencyMode.SAFE,
        max_result_chars=settings.web_fetch_max_chars,
        timeout=settings.web_fetch_timeout,
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的完整 URL（必须以 http:// 或 https:// 开头，通常来自 web_search 返回结果）",
                },
                "prompt": {
                    "type": "string",
                    "description": "阅读说明：希望从页面中获取什么样的信息（可选）",
                },
            },
            "required": ["url"],
        },
    )

    async def run(self, db: AsyncSession, **kwargs) -> ToolResult:
        url = kwargs.get("url", "").strip()
        if not url:
            return ToolResult(success=False, error="URL is required")

        prompt = kwargs.get("prompt", "").strip()

        content, error = await _fetch_url(url)
        if error:
            return ToolResult(success=False, error=error)

        result = {"url": url, "content": content}
        if prompt:
            result["prompt"] = prompt

        return ToolResult(success=True, data=result)
