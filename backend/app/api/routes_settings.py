"""Runtime provider settings API for the single-user local tool."""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.llm import registry as llm_registry
from app.settings.models import ModelConfig
from app.settings.service import get_config, save_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ModelSettingsPayload(BaseModel):
    main_model: str
    main_base_url: str
    main_api_key: str = ""
    middle_model: str | None = None
    fallback_models: list[str] = []
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    embedding_proxy: str | None = None


class TestConnectionPayload(BaseModel):
    base_url: str
    api_key: str
    model: str


def _to_payload(cfg: ModelConfig | None) -> dict:
    configured = bool(
        cfg
        and (cfg.main_api_key or cfg.main_model != "deepseek-v4-flash")
    )
    base = {
        "configured": configured,
        "main_model": cfg.main_model if cfg else "deepseek-v4-flash",
        "main_base_url": cfg.main_base_url if cfg else "https://api.deepseek.com/v1",
        "main_api_key": cfg.main_api_key if cfg else "",
        "middle_model": cfg.middle_model if cfg else "",
        "fallback_models": list(cfg.fallback_models or []) if cfg else [],
        "embedding_base_url": cfg.embedding_base_url if cfg else "",
        "embedding_model": cfg.embedding_model if cfg else "",
        "embedding_api_key": cfg.embedding_api_key if cfg else "",
        "embedding_proxy": cfg.embedding_proxy if cfg else "",
    }
    try:
        base["effective_models"] = llm_registry.get_ordered()
    except Exception:
        base["effective_models"] = []
    return base


@router.get("/models")
async def get_model_settings(db: AsyncSession = Depends(get_db)):
    cfg = await get_config(db)
    return _to_payload(cfg)


@router.put("/models")
async def update_model_settings(payload: ModelSettingsPayload, db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    try:
        cfg = await save_config(db, data)
    except Exception as exc:
        logger.exception("failed to save model config")
        raise HTTPException(status_code=500, detail=f"保存模型配置失败: {exc}")
    return _to_payload(cfg)


@router.post("/models/test")
async def test_model_connection(payload: TestConnectionPayload):
    """Fire a minimal chat completion against the given provider."""
    import httpx

    base_url = payload.base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {payload.api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": payload.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "stream": False,
    }
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code != 200:
            detail = resp.text[:300]
            return {"ok": False, "status": resp.status_code, "detail": detail, "latency_ms": latency_ms}
        data = resp.json()
        model = (data.get("model") or payload.model) if isinstance(data, dict) else payload.model
        return {"ok": True, "model": model, "latency_ms": latency_ms}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:300], "latency_ms": None}
