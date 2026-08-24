import asyncio
from typing import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from app.config import settings
from app.database import async_session
from app.settings.service import local_user

import logging
logger = logging.getLogger(__name__)

_redis_instance: Redis | None = None
_redis_lock = asyncio.Lock()


def _sanitize_url(url: str) -> str:
    """Strip credentials from a connection URL for safe logging."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port or ''}{parsed.path}"
    except Exception:
        return "<invalid url>"


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


async def get_redis() -> Redis | None:
    """Lazily initializes and returns a shared Redis client, or None if unavailable."""
    global _redis_instance
    if _redis_instance is not None:
        return _redis_instance
    async with _redis_lock:
        if _redis_instance is not None:
            return _redis_instance
        try:
            _redis_instance = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            await _redis_instance.ping()
            logger.info("Connected to Redis at %s", _sanitize_url(settings.redis_url))
        except Exception as exc:
            logger.warning("Redis unavailable (using in-memory fallback): %s", exc)
            _redis_instance = None
    return _redis_instance


async def get_current_user(authorization: str | None = None, request: Request = None, db: AsyncSession = Depends(get_db)) -> dict:
    """Single-user local tool: always returns the fixed local identity."""
    return local_user()
