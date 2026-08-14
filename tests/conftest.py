"""Shared fixtures.

Settings are built explicitly rather than read from the environment, so a
stray ``EASE_DRIVE_*`` variable or a local ``.env`` cannot change a test result.

Database-backed gate tests run against in-memory SQLite through the real
models and the real session machinery, so they stay offline, free and fast.
Neon itself is exercised by the ``integration`` lane in
``tests/integration/``, which the gate excludes.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.main import create_app

# Every model must be imported before create_all, or its table is missing.
import app.models  # noqa: F401  # isort: skip


@pytest.fixture
def settings() -> Settings:
    """Deterministic settings, independent of the ambient environment."""
    return Settings(
        project_name="Ease Drive API",
        version="0.1.0",
        api_v1_prefix="/api/v1",
        greeting="Hello, World!",
        database_url=None,
    )


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """An in-memory SQLite engine with the real schema applied.

    StaticPool keeps every checkout on the same connection. Without it each
    session would get a fresh `:memory:` database and see no tables.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session bound to the in-memory database."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A client with no database configured. DB routes return 503."""
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


@pytest.fixture
def db_client(settings: Settings, db_session: AsyncSession) -> Iterator[TestClient]:
    """A client whose session dependency is bound to in-memory SQLite."""
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _session_override
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()
