import asyncio
import hashlib
import time
from typing import AsyncGenerator, Optional
from urllib.parse import urlparse

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from app.config import settings
from app.database import async_session
from app.user.service import UserService

import logging
logger = logging.getLogger(__name__)

_redis_instance: Redis | None = None
_redis_lock = asyncio.Lock()
_revoked_tokens: dict[str, float] = {}
_BLACKLIST_MAX = 20000
_BLACKLIST_TTL = 86400  # 24h
_JWT_AUDIENCE = "storycad-api"


def _sanitize_url(url: str) -> str:
    """Strip credentials from a connection URL for safe logging."""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port or ''}{parsed.path}"
    except Exception:
        return "<invalid url>"


def _token_blacklist_key(token: str) -> str:
    """Prefer the JWT jti; fall back to a hash of the full token."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=["HS256"],
            options={"verify_exp": False},
        )
        jti = payload.get("jti")
        if jti:
            return f"jti:{jti}"
    except jwt.PyJWTError:
        pass
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


async def _blacklist_ttl() -> int:
    return max(int(settings.jwt_expire_hours or 24) * 3600, 3600)


async def blacklist_token(token: str) -> None:
    key = _token_blacklist_key(token)
    redis = await get_redis()
    if redis is not None:
        try:
            await redis.set(f"jwt_blacklist:{key}", "1", ex=await _blacklist_ttl())
            return
        except Exception as exc:
            logger.warning("Redis blacklist write failed, falling back to memory: %s", exc)
    _revoked_tokens[key] = time.time()
    if len(_revoked_tokens) > _BLACKLIST_MAX:
        cutoff = time.time() - _BLACKLIST_TTL
        for t in list(_revoked_tokens.keys()):
            if _revoked_tokens[t] < cutoff:
                del _revoked_tokens[t]


async def is_token_revoked(token: str) -> bool:
    key = _token_blacklist_key(token)
    redis = await get_redis()
    if redis is not None:
        try:
            return bool(await redis.get(f"jwt_blacklist:{key}"))
        except Exception as exc:
            logger.warning("Redis blacklist read failed, falling back to memory: %s", exc)
    entry = _revoked_tokens.get(key)
    if entry is None:
        return False
    if time.time() - entry > _BLACKLIST_TTL:
        del _revoked_tokens[key]
        return False
    return True


async def decode_token(token: str) -> Optional[dict]:
    """Verify a JWT (signature, expiry, iss/aud/jti) and blacklist; return the payload."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            audience=_JWT_AUDIENCE,
            options={"verify_exp": True, "require": ["sub", "exp", "iss", "aud", "jti"]},
        )
    except jwt.PyJWTError:
        return None
    if await is_token_revoked(token):
        return None
    return payload


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


async def get_current_user(authorization: Optional[str] = Header(None), request: Request = None, db: AsyncSession = Depends(get_db)) -> dict:
    # token 来源优先级:Authorization: Bearer header → storycad_token cookie
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token and request is not None:
        token = request.cookies.get("storycad_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")
    if await is_token_revoked(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")
    service = UserService(db)
    # NOTE: UserService holds a session reference — avoid using service in background tasks
    return await service.get_current_user(token)