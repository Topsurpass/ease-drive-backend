"""Shared fixtures.

Settings are built explicitly rather than read from the environment, so a
stray ``EASE_DRIVE_*`` variable or a local ``.env`` cannot change a test result.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Deterministic settings, independent of the ambient environment."""
    return Settings(
        project_name="Ease Drive API",
        version="0.1.0",
        api_v1_prefix="/api/v1",
        greeting="Hello, World!",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A test client bound to a freshly built app using the test settings."""
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()
