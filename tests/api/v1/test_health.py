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


def test_reports_the_error_instead_of_raising() -> None:
    """An unreachable host must be reported, not surfaced as a 500."""
    body = _probe(UNREACHABLE)
    database = body["database"]
    assert isinstance(database, dict)

    assert body["status"] == "degraded"
    assert database["configured"] is True
    assert database["ok"] is False
    assert database["error"]
