"""Async engine and session lifecycle.

Engines are created lazily and memoized per URL in an explicit registry rather
than an `lru_cache`, because shutdown has to iterate the live engines to close
their pooled sockets and a cache cannot be enumerated.

Creating the engine at import time would mean a missing DATABASE_URL breaks
`import app.main`, so `fastapi run` could not even start to report the problem.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.url import normalize_database_url

_engines: dict[str, AsyncEngine] = {}
_sessionmakers: dict[str, async_sessionmaker[AsyncSession]] = {}


def get_engine(database_url: str) -> AsyncEngine:
    """Return the engine for a URL, building it once per process."""
    engine = _engines.get(database_url)
    if engine is not None:
        return engine

    url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(
        url,
        connect_args=connect_args,
        # Neon idles a compute down after inactivity, which leaves stale
        # sockets in the pool. pre_ping discards them instead of surfacing a
        # ConnectionDoesNotExistError on the first request after a quiet spell.
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=300,
    )
    _engines[database_url] = engine
    return engine


def get_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Return the session factory for a URL, built once per process."""
    factory = _sessionmakers.get(database_url)
    if factory is not None:
        return factory

    factory = async_sessionmaker(
        bind=get_engine(database_url),
        expire_on_commit=False,
        autoflush=False,
    )
    _sessionmakers[database_url] = factory
    return factory


async def session_scope(database_url: str) -> AsyncIterator[AsyncSession]:
    """Yield a session, rolling back if the caller raises."""
    factory = get_sessionmaker(database_url)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engines() -> None:
    """Close every pooled connection. Called on application shutdown."""
    for engine in list(_engines.values()):
        await engine.dispose()
    _engines.clear()
    _sessionmakers.clear()
