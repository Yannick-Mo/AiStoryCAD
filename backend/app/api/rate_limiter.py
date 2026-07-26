from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.guard import RateLimiter as _GuardRateLimiter

logger = logging.getLogger(__name__)


class InMemoryRateLimiter:
    def __init__(self):
        self._attempts = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check(self, key: str, max_attempts: int = 5, window: int = 60) -> bool:
        now = time.time()
        async with self._lock:
            self._attempts[key] = [t for t in self._attempts[key] if now - t < window]
            if not self._attempts[key]:
                del self._attempts[key]
            if len(self._attempts.get(key, ())) >= max_attempts:
                return False
            self._attempts[key].append(now)
            return True


rate_limiter = InMemoryRateLimiter()


# ── Shared singleton for agent guard ──────────────────────────────

_guard_limiter: object | None = None  # _GuardRateLimiter | None — resolved in get_rate_limiter()


def get_rate_limiter() -> _GuardRateLimiter:
    """Return the application-wide singleton RateLimiter (with optional Redis).

    The first call lazily creates and caches the instance, attempting to
    wire in a Redis backend.  If Redis is unavailable the limiter falls
    back to in-memory storage transparently.
    """
    global _guard_limiter
    if _guard_limiter is not None:
        return _guard_limiter

    # Defer the heavy import so guard.py can be loaded first
    from app.agent.guard import RateLimiter as _GuardRateLimiter  # noqa: F811

    redis_client = None
    try:
        from redis.asyncio import Redis
        from app.config import settings

        if settings.redis_url:
            redis_client = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            logger.info("RateLimiter created with Redis backend")
    except Exception as exc:
        logger.warning("RateLimiter Redis unavailable, using in-memory: %s", exc)

    _guard_limiter = _GuardRateLimiter(redis_client=redis_client)
    return _guard_limiter
