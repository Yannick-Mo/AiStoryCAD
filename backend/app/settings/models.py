import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from app.project.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ModelConfig(Base):
    """Single-row (id=1) LLM / embedding provider settings.

    Editable at runtime from the frontend settings modal; applied to the
    in-process LLM registry without a restart.
    """

    __tablename__ = "model_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_model_config_single_row"),
    )

    id = Column(Integer, primary_key=True, default=1)
    main_model = Column(String(100), nullable=False, default="deepseek-v4-flash")
    main_base_url = Column(String(500), nullable=False, default="https://api.deepseek.com/v1")
    main_api_key = Column(String(500), nullable=False, default="")
    middle_model = Column(String(100), nullable=True)
    # List of fallback models; entries may be "name" (shares main provider)
    # or "name|api_key|base_url" (independent provider).
    fallback_models = Column(JSONB, nullable=False, default=list)
    embedding_base_url = Column(String(500), nullable=True)
    embedding_model = Column(String(100), nullable=True, default="text-embedding-3-small")
    embedding_api_key = Column(String(500), nullable=True)
    embedding_proxy = Column(String(500), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
