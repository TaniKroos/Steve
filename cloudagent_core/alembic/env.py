"""
Alembic's entrypoint, wired for our async engine and for reading the DB
URL from environment/.env instead of a hardcoded string in alembic.ini.

Two run modes exist (both defined below, Alembic picks one automatically):
- "offline": generates the raw SQL for a migration without connecting to
  a database at all -- useful for review, not something we use day to day.
- "online": actually connects and applies/generates against a live DB --
  this is the one `alembic upgrade head` / `alembic revision --autogenerate`
  use in normal development.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from cloudagent_core.db.models import Base
from cloudagent_core.settings import CoreSettings

# Alembic's own config object, populated from alembic.ini.
config = context.config

# Wires up Python's logging module per alembic.ini's [logger_*] sections
# -- this is what makes `alembic upgrade head` print readable progress
# instead of nothing.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# THE key line for autogeneration: this tells Alembic "compare the live
# database's schema against these model definitions" so
# `alembic revision --autogenerate` can compute the diff for you instead
# of you hand-writing every column change.
target_metadata = Base.metadata


def _get_database_url() -> str:
    # Reuses the exact same settings class the apps use, so there's no
    # second place a DB URL could be defined incorrectly.
    return CoreSettings().database_url  # type: ignore[call-arg]


def run_migrations_offline() -> None:
    context.configure(
        url=_get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # `asyncpg` (our driver) only speaks async, so the engine here has to
    # be async too -- `connection.run_sync(...)` below is the bridge that
    # lets Alembic's fundamentally-synchronous migration machinery run
    # inside it.
    connectable: AsyncEngine = create_async_engine(
        _get_database_url(),
        poolclass=pool.NullPool,  # a short-lived migration run doesn't need connection pooling
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
