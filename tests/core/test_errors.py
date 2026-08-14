"""Gate tests for the error envelope.

Regression coverage for 2026-08-14: a missing `ease_bookings` table made the
booking endpoint raise, Starlette returned a bare `500 text/plain` from outside
the CORS layer, and the browser reported it as

    No 'Access-Control-Allow-Origin' header is present on the requested resource

The CORS allowlist was correct the whole time. These tests pin the two things
that made the real cause invisible: a 500 must be JSON, and it must carry its
CORS headers.
"""

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app

ORIGIN = "https://ease-drive-frontend.vercel.app"


@pytest.fixture
def exploding_app() -> FastAPI:
    """An app with one route that raises, to exercise the unhandled path."""
    settings = Settings(database_url=None)
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings

    @application.get("/boom")
    async def boom() -> None:
        raise RuntimeError("simulated failure")

    return application


def test_unhandled_errors_return_500(exploding_app: FastAPI) -> None:
    with TestClient(exploding_app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_unhandled_errors_carry_cors_headers(exploding_app: FastAPI) -> None:
    """Without this the browser blames CORS and the real error is invisible."""
    with TestClient(exploding_app, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"Origin": ORIGIN})
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_unhandled_errors_are_json_in_the_frontend_shape(
    exploding_app: FastAPI,
) -> None:
    with TestClient(exploding_app, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"Origin": ORIGIN})

    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "transport_error"
    assert isinstance(body["message"], str) and body["message"]


def test_unhandled_errors_do_not_leak_internals(exploding_app: FastAPI) -> None:
    """The traceback belongs in the platform log, not in a browser response."""
    with TestClient(exploding_app, raise_server_exceptions=False) as client:
        body = client.get("/boom").text
    assert "simulated failure" not in body
    assert "Traceback" not in body


def test_the_traceback_is_logged(
    exploding_app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    """Hiding it from the response is only safe if it is recorded somewhere."""
    with (
        caplog.at_level("ERROR", logger="app.errors"),
        TestClient(exploding_app, raise_server_exceptions=False) as client,
    ):
        client.get("/boom")

    tracebacks = [record.exc_text or "" for record in caplog.records]
    assert any("simulated failure" in text for text in tracebacks), tracebacks


def test_deliberate_http_errors_keep_their_own_body(client: TestClient) -> None:
    """A 503 from the session dependency must not be flattened into a 500."""
    response = client.post(
        "/api/v1/bookings",
        json={
            "fullName": "Ada Lovelace",
            "phone": "+234 800 000 0000",
            "email": "ada@example.com",
            "tripType": "airport",
            "pickupLocation": "Ikeja GRA",
            "destination": "Murtala Muhammed Airport",
            "startDate": "2099-01-01",
            "durationDays": 2,
            "passengers": 3,
        },
    )
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["detail"]["code"] == "unavailable"


def test_settings_allow_the_deployed_frontend_origin() -> None:
    assert ORIGIN in Settings(_env_file=None).cors_origins  # type: ignore[call-arg]
