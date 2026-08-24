"""Runtime LLM / embedding provider configuration (single-user local tool).

The ``model_config`` row in PostgreSQL is the source of truth; on startup
it overrides environment-variable defaults when present, and every save from
the settings API re-applies it live to the in-process registry.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings.models import ModelConfig

logger = logging.getLogger(__name__)

LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"


def local_user() -> dict:
    """Fixed identity for the single-user local tool."""
    return {
        "id": LOCAL_USER_ID,
        "username": "local",
        "email": "local@localhost",
        "display_name": "本地用户",
    }


async def get_config(db: AsyncSession) -> ModelConfig | None:
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == 1))
    return result.scalar_one_or_none()


async def save_config(db: AsyncSession, data: dict) -> ModelConfig:
    """Upsert the single-row config and re-apply it to the registry."""
    cfg = await get_config(db)
    if cfg is None:
        cfg = ModelConfig(id=1)
        db.add(cfg)
    for field in (
        "main_model",
        "main_base_url",
        "main_api_key",
        "middle_model",
        "fallback_models",
        "embedding_base_url",
        "embedding_model",
        "embedding_api_key",
        "embedding_proxy",
    ):
        if field in data and data[field] is not None:
            setattr(cfg, field, data[field])
    await db.commit()
    await db.refresh(cfg)
    apply_config(cfg)
    logger.info("model config saved and applied: main=%s", cfg.main_model)
    return cfg


def apply_config(cfg: ModelConfig | None) -> None:
    """Rebuild the in-process LLM registry from a DB config row."""
    from app.llm import registry as llm_registry

    if cfg is None:
        return
    llm_registry.configure_from_config(cfg)
    llm_registry.set_middle_model(cfg.middle_model or "")
    llm_registry.set_embedding(
        base_url=cfg.embedding_base_url or "",
        model=cfg.embedding_model or "",
        api_key=cfg.embedding_api_key or "",
        proxy=cfg.embedding_proxy or "",
    )


async def load_and_apply(db: AsyncSession) -> bool:
    """Startup hook: apply DB config if present; return True if applied."""
    cfg = await get_config(db)
    if cfg is None:
        return False
    apply_config(cfg)
    logger.info("model config loaded from DB: main=%s", cfg.main_model)
    return True
