"""Gate tests for POST /api/v1/bookings."""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.main import create_app
from app.models.booking import Booking

ENDPOINT = "/api/v1/bookings"
REFERENCE_RE = re.compile(r"^ED-[0-9A-HJKMNP-TV-Z]{6}$")


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "fullName": "Ada Lovelace",
        "phone": "+234 800 000 0000",
        "email": "ada@example.com",
        "tripType": "airport",
        "pickupLocation": "Ikeja GRA",
        "destination": "Murtala Muhammed Airport",
        "startDate": (datetime.now(UTC) + timedelta(days=3)).date().isoformat(),
        "durationDays": 2,
        "passengers": 3,
        "notes": "Two large suitcases.",
    }
    body.update(overrides)
    return body


def test_accepts_a_booking(db_client: TestClient) -> None:
    response = db_client.post(ENDPOINT, json=_payload())
    assert response.status_code == status.HTTP_201_CREATED


def test_returns_the_frontend_success_shape(db_client: TestClient) -> None:
    """Matches `BookingSuccess` in ease-drive-frontend/src/services/booking."""
    body = db_client.post(ENDPOINT, json=_payload()).json()
    assert body["ok"] is True
    assert REFERENCE_RE.match(body["reference"])
    assert "receivedAt" in body


async def test_writes_a_row(db_client: TestClient, db_session: AsyncSession) -> None:
    reference = db_client.post(ENDPOINT, json=_payload()).json()["reference"]

    stored = (
        await db_session.execute(select(Booking).where(Booking.reference == reference))
    ).scalar_one()
    assert stored.email == "ada@example.com"
    assert stored.destination == "Murtala Muhammed Airport"


async def test_two_submissions_write_two_rows(
    db_client: TestClient, db_session: AsyncSession
) -> None:
    db_client.post(ENDPOINT, json=_payload())
    db_client.post(ENDPOINT, json=_payload(fullName="Grace Hopper"))

    total = (await db_session.execute(select(func.count(Booking.id)))).scalar_one()
    assert total == 2


def test_rejects_an_invalid_payload(db_client: TestClient) -> None:
    response = db_client.post(ENDPOINT, json=_payload(email="nope"))
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_validation_errors_use_the_frontend_failure_shape(
    db_client: TestClient,
) -> None:
    """FastAPI's default {"detail": [...]} is unreadable to the booking form."""
    body = db_client.post(ENDPOINT, json=_payload(passengers=99)).json()
    assert body["ok"] is False
    assert body["code"] == "validation_error"
    assert isinstance(body["message"], str) and body["message"]


def test_validation_errors_keep_field_detail(db_client: TestClient) -> None:
    """The contract shape must not cost debuggability."""
    body = db_client.post(ENDPOINT, json=_payload(passengers=99)).json()
    assert any("passengers" in str(item) for item in body["detail"])


async def test_rejected_payloads_write_nothing(
    db_client: TestClient, db_session: AsyncSession
) -> None:
    db_client.post(ENDPOINT, json=_payload(tripType="submarine"))
    total = (await db_session.execute(select(func.count(Booking.id)))).scalar_one()
    assert total == 0


def test_returns_503_when_no_database_is_configured(client: TestClient) -> None:
    """A missing DATABASE_URL is a deployment fault, not a bad request."""
    response = client.post(ENDPOINT, json=_payload())
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    detail = response.json()["detail"]
    assert detail["ok"] is False
    assert detail["code"] == "unavailable"


def test_rejects_unsupported_method(db_client: TestClient) -> None:
    assert db_client.get(ENDPOINT).status_code == status.HTTP_405_METHOD_NOT_ALLOWED


def test_allows_the_frontend_origin(db_client: TestClient) -> None:
    """Without CORS the browser blocks the POST before it reaches the endpoint."""
    response = db_client.post(
        ENDPOINT, json=_payload(), headers={"Origin": "http://localhost:3000"}
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_answers_the_preflight(db_client: TestClient) -> None:
    """A JSON POST is preflighted. If OPTIONS fails the POST is never sent."""
    response = db_client.options(
        ENDPOINT,
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_allows_a_configured_deployment_origin(db_session: AsyncSession) -> None:
    """A deployed origin comes from EASE_DRIVE_CORS_ORIGINS, not from the code.

    Exercised end to end: a POST from that origin must both succeed and come
    back with the header, which is the pair the browser needs.
    """
    origin = "https://ease-drive-backend.fastapicloud.dev"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=None,
        cors_origins=(origin,),
    )
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _session_override

    with TestClient(application) as deployed:
        response = deployed.post(ENDPOINT, json=_payload(), headers={"Origin": origin})

    assert response.status_code == status.HTTP_201_CREATED
    assert response.headers["access-control-allow-origin"] == origin


def test_rejects_an_unlisted_origin(db_client: TestClient) -> None:
    """An allowlist that lets anything through is not an allowlist."""
    response = db_client.post(
        ENDPOINT, json=_payload(), headers={"Origin": "https://evil.example"}
    )
    assert "access-control-allow-origin" not in response.headers
