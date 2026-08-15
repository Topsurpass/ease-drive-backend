"""Gate tests for the application factory and its OpenAPI surface."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

STARTUP_LOGGER = "app.startup"


def test_factory_returns_independent_apps() -> None:
    """Each call builds a new app, so tests cannot leak state into each other."""
    assert create_app() is not create_app()


def test_applies_settings_to_metadata() -> None:
    application = create_app(Settings(project_name="Custom", version="9.9.9"))
    assert application.title == "Custom"
    assert application.version == "9.9.9"


def test_mounts_every_route_under_the_version_prefix() -> None:
    """Asserts the property, not a fixed list of paths.

    This used to compare against a hardcoded set, so every new endpoint failed
    a test that had nothing to do with it. What actually matters is that no
    route escapes the prefix: one mounted at the root would be unversioned
    forever and could not be changed without breaking clients.
    """
    settings = Settings(api_v1_prefix="/api/v1")
    paths = create_app(settings).openapi()["paths"]

    assert paths, "no routes were mounted at all"
    assert all(path.startswith("/api/v1/") for path in paths), sorted(paths)
    # Spot-check the two whose methods are part of the published contract.
    assert list(paths["/api/v1/hello"]) == ["get"]
    assert list(paths["/api/v1/bookings"]) == ["post"]


def test_version_prefix_is_configurable() -> None:
    default = set(create_app(Settings(api_v1_prefix="/api/v1")).openapi()["paths"])
    moved = set(create_app(Settings(api_v1_prefix="/v2")).openapi()["paths"])

    assert all(path.startswith("/v2/") for path in moved), sorted(moved)
    # The same routes at a different prefix — nothing gained or lost.
    assert {path.removeprefix("/v2") for path in moved} == {
        path.removeprefix("/api/v1") for path in default
    }


def test_empty_prefix_serves_at_the_root() -> None:
    """README tells the reader to set the prefix to "" to drop versioning."""
    settings = Settings(api_v1_prefix="")
    with TestClient(create_app(settings)) as unversioned:
        assert unversioned.get("/hello").status_code == 200


def _boot(settings: Settings, caplog: pytest.LogCaptureFixture) -> str:
    """Run the lifespan and return everything it logged."""
    with (
        caplog.at_level("INFO", logger=STARTUP_LOGGER),
        TestClient(create_app(settings)),
    ):
        pass
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == STARTUP_LOGGER
    )


def test_startup_logs_the_resolved_allowlist(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Config that fails silently has to announce itself somewhere."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        cors_origins=("https://app.example.com", "https://admin.example.com"),
    )
    logged = _boot(settings, caplog)
    assert "https://app.example.com" in logged
    assert "https://admin.example.com" in logged


def test_startup_warns_when_cors_is_unconfigured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The exact deploy mistake this refactor makes possible, made loud."""
    logged = _boot(Settings(_env_file=None), caplog)  # type: ignore[call-arg]
    assert "EASE_DRIVE_CORS_ORIGINS is not set" in logged


def test_startup_stays_quiet_when_cors_is_configured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A warning that fires on a correct deploy trains people to ignore it."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        cors_origins=("https://app.example.com",),
    )
    logged = _boot(settings, caplog)
    assert "EASE_DRIVE_CORS_ORIGINS is not set" not in logged


def test_startup_warns_when_the_database_is_unconfigured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(_env_file=None, database_url=None)  # type: ignore[call-arg]
    assert "DATABASE_URL is not set" in _boot(settings, caplog)


def test_startup_logging_uses_the_apps_own_settings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two apps in one process must not report each other's configuration."""
    configured = Settings(
        _env_file=None,  # type: ignore[call-arg]
        cors_origins=("https://only-mine.example",),
    )
    assert "https://only-mine.example" in _boot(configured, caplog)


def test_openapi_documents_the_response_schema(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    ref = schema["paths"]["/api/v1/hello"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/HelloResponse")
    assert "message" in schema["components"]["schemas"]["HelloResponse"]["properties"]
