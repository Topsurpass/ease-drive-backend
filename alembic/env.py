"""Alembic environment, wired to the async engine.

Reads the connection string from `app.core.config` rather than alembic.ini, so
the credential lives only in the environment and there is one source of truth.
Importing `app.models` is what populates `Base.metadata`, without which
autogenerate would produce an empty migration.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models  # noqa: F401  - registers every model on Base.metadata
from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from app.db.url import normalize_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
if not settings.is_database_configured:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env and fill it in, "
        "or export DATABASE_URL, then re-run alembic."
    )

assert settings.database_url is not None  # narrowed by the guard above
_url, _connect_args = normalize_database_url(settings.database_url)
config.set_main_option("sqlalchemy.url", _url)


# This Neon database is shared with nibbs-report, whose six `nibbs_` tables are
# not in our metadata. Without this filter, autogenerate reads them as "exists
# in the database but not in the model" and emits DROP TABLE for each one,
# which would destroy the other application. Only `ease_` tables are ours.
TABLE_PREFIX = "ease_"
VERSION_TABLE = "ease_alembic_version"


def include_name(
    name: str | None, type_: str, _parent_names: dict[str, str | None]
) -> bool:
    """Hide every table that is not ours from autogenerate's comparison."""
    if type_ == "table":
        return name is not None and name.startswith(TABLE_PREFIX)
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting, for review or manual apply."""
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=include_name,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=_connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
