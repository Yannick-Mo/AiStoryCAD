import os
import sys
from logging.config import fileConfig
from sqlalchemy import pool, create_engine
from alembic import context

sys.path.insert(0, ".")

from app.project.models import Base
from app.storycad.models import StoryBase
from app.user.models import User
from app.agent.memory.models import Conversation, ConversationMessage  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = [Base.metadata, StoryBase.metadata]


def run_migrations_offline():
    url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
    url = url.replace("+asyncpg", "+psycopg2")
    connectable = create_engine(url)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
