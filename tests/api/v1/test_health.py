"""Gate tests for GET /api/v1/health."""

from fastapi import status
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app

ENDPOINT = "/api/v1/health"


def test_reports_degraded_without_a_database(client: TestClient) -> None:
    body = client.get(ENDPOINT).json()
    assert body["status"] == "degraded"
    assert body["database"]["configured"] is False
    assert body["database"]["ok"] is False


def test_stays_200_when_degraded(client: TestClient) -> None:
    """A diagnostic that 500s tells you nothing about why it 500ed."""
    assert client.get(ENDPOINT).status_code == status.HTTP_200_OK


# Port 1 on loopback refuses instantly: the probe exercises the real failure
# path without a DNS lookup, so the gate stays offline and fast.
UNREACHABLE = "postgresql://user:hunter2@127.0.0.1:1/mydb?sslmode=disable"


def _probe(database_url: str) -> dict[str, object]:
    settings = Settings(database_url=database_url)
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application) as client:
        body: dict[str, object] = client.get(ENDPOINT).json()
    return body


def test_reports_host_and_database_without_the_password() -> None:
    """This endpoint is unauthenticated, so a leaked credential is a breach."""
    body = _probe(UNREACHABLE)
    database = body["database"]
    assert isinstance(database, dict)

    assert database["host"] == "127.0.0.1"
    assert database["database"] == "mydb"
    assert "hunter2" not in str(body)


def test_reports_the_cors_allowlist(client: TestClient) -> None:
    """So a deploy's real allowlist is checkable with curl, not a redeploy."""
    cors = client.get(ENDPOINT).json()["cors"]
    assert cors["origins"] == ["http://localhost:3000", "http://127.0.0.1:3000"]
    assert cors["configured"] is True


def test_reports_unconfigured_cors() -> None:
    """The signal that a server is missing EASE_DRIVE_CORS_ORIGINS."""
    settings = Settings(_env_file=None, database_url=None)  # type: ignore[call-arg]
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application) as unconfigured:
        cors = unconfigured.get(ENDPOINT).json()["cors"]

    assert cors["configured"] is False
    assert cors["origins"] == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_cors_does_not_affect_the_status(client: TestClient) -> None:
    """`status` grades the process, not the deployment's env vars.

    Two apps differing only in the allowlist must report the same status, or
    every local dev run reads as degraded and the field stops meaning anything.
    """
    settings = Settings(_env_file=None, database_url=None)  # type: ignore[call-arg]
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application) as unconfigured:
        without = unconfigured.get(ENDPOINT).json()

    assert without["cors"]["configured"] is False
    assert client.get(ENDPOINT).json()["cors"]["configured"] is True
    assert without["status"] == client.get(ENDPOINT).json()["status"]


def test_reports_the_error_instead_of_raising() -> None:
    """An unreachable host must be reported, not surfaced as a 500."""
    body = _probe(UNREACHABLE)
    database = body["database"]
    assert isinstance(database, dict)

    assert body["status"] == "degraded"
    assert database["configured"] is True
    assert database["ok"] is False
    assert database["error"]
