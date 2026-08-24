import asyncio
import os

import httpx
from app.config import settings
from app.llm.registry import get_embedding

_MAX_RETRIES = 3


def _runtime_embedding() -> dict[str, str]:
    cfg = get_embedding()
    if cfg.get("base_url") or cfg.get("api_key") or cfg.get("model"):
        return cfg
    return {}


def _embedding_base_url() -> str:
    cfg = _runtime_embedding()
    url = cfg.get("base_url") or settings.embedding_base_url or settings.llm_base_url
    return url.rstrip("/")


def _embedding_model() -> str:
    cfg = _runtime_embedding()
    return cfg.get("model") or settings.embedding_model


def _embedding_api_key() -> str:
    cfg = _runtime_embedding()
    return cfg.get("api_key") or settings.embedding_api_key or settings.llm_api_key


def _embedding_proxy() -> str:
    cfg = _runtime_embedding()
    return (
        cfg.get("proxy")
        or settings.embedding_proxy
        or getattr(settings, "llm_proxy", None)
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("HTTPS_PROXY")
    )


async def _call_embedding_api(texts: list[str]) -> list[list[float]]:
    url = f"{_embedding_base_url()}/embeddings"
    api_key = _embedding_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _embedding_model(),
        "input": texts,
    }
    proxy_url = _embedding_proxy()
    async with httpx.AsyncClient(timeout=60.0, proxy=proxy_url if proxy_url else None) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    results = sorted(data["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in results]


async def _call_embedding_api_with_retry(texts: list[str]) -> list[list[float]]:
    for attempt in range(_MAX_RETRIES):
        try:
            return await _call_embedding_api(texts)
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as e:
            if attempt == _MAX_RETRIES - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    return []


async def embed_text(text: str) -> list[float]:
    vectors = await _call_embedding_api_with_retry([text])
    return vectors[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return await _call_embedding_api_with_retry(texts)
