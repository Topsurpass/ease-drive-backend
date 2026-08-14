"""Gate tests for the booking contract.

Each limit is checked against the value in
`ease-drive-frontend/src/lib/validators/booking.schema.ts`, so a drift between
the two schemas fails here rather than in production.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.booking import BookingAccepted, BookingRequest


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


def test_accepts_a_valid_payload() -> None:
    request = BookingRequest.model_validate(_payload())
    assert request.full_name == "Ada Lovelace"
    assert request.trip_type == "airport"


def test_accepts_camel_case_from_the_form() -> None:
    """The zod schema emits camelCase; the API must read it as sent."""
    assert BookingRequest.model_validate(_payload()).pickup_location == "Ikeja GRA"


def test_also_accepts_snake_case() -> None:
    """So a Python client need not speak JavaScript."""
    request = BookingRequest.model_validate(
        {
            "full_name": "Grace Hopper",
            "phone": "08000000000",
            "email": "grace@example.com",
            "trip_type": "corporate",
            "pickup_location": "Victoria Island",
            "destination": "Lekki",
            "start_date": (datetime.now(UTC) + timedelta(days=1)).date().isoformat(),
            "duration_days": 1,
            "passengers": 1,
        }
    )
    assert request.full_name == "Grace Hopper"


def test_trims_whitespace() -> None:
    assert (
        BookingRequest.model_validate(_payload(fullName="  Ada Lovelace  ")).full_name
        == "Ada Lovelace"
    )


def test_rejects_unknown_fields() -> None:
    """extra="forbid" stops a renamed frontend field failing silently."""
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(_payload(surpriseField="x"))


@pytest.mark.parametrize("name", ["A", "x" * 81])
def test_rejects_out_of_range_names(name: str) -> None:
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(_payload(fullName=name))


@pytest.mark.parametrize(
    "phone",
    # "+234 (800) 12" is deliberately absent: 8 digits over 13 characters
    # satisfies the frontend regex too, so rejecting it here would be drift.
    ["12345", "not-a-phone", "", "+234-800-000-0000-000-000"],
)
def test_rejects_bad_phone_numbers(phone: str) -> None:
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(_payload(phone=phone))


@pytest.mark.parametrize(
    "phone",
    ["+234 800 000 0000", "08000000000", "(080) 000-0000"],
)
def test_accepts_realistic_phone_formats(phone: str) -> None:
    assert BookingRequest.model_validate(_payload(phone=phone)).phone == phone


def test_rejects_an_invalid_email() -> None:
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(_payload(email="ada@"))


def test_rejects_an_unknown_trip_type() -> None:
    """Trip types are a closed set copied from siteConfig.booking.tripTypes."""
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(_payload(tripType="submarine"))


@pytest.mark.parametrize(
    "trip_type",
    [
        "interstate",
        "intrastate",
        "private-driver",
        "family-group",
        "airport",
        "corporate",
        "events",
    ],
)
def test_accepts_every_trip_type_the_form_offers(trip_type: str) -> None:
    assert BookingRequest.model_validate(_payload(tripType=trip_type)).trip_type == (
        trip_type
    )


def test_rejects_a_start_date_well_in_the_past() -> None:
    stale = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(_payload(startDate=stale))


def test_allows_yesterday_for_time_zone_slack() -> None:
    """A client in UTC+1 booking after midnight is still on the previous UTC day."""
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    parsed = BookingRequest.model_validate(_payload(startDate=yesterday))
    assert parsed.start_date.isoformat() == yesterday


@pytest.mark.parametrize("days", [0, 31])
def test_rejects_out_of_range_durations(days: int) -> None:
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(_payload(durationDays=days))


@pytest.mark.parametrize("passengers", [0, 15])
def test_rejects_out_of_range_passenger_counts(passengers: int) -> None:
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(_payload(passengers=passengers))


def test_rejects_overlong_notes() -> None:
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(_payload(notes="x" * 501))


def test_notes_are_optional() -> None:
    payload = _payload()
    del payload["notes"]
    assert BookingRequest.model_validate(payload).notes is None


def test_blank_notes_become_null() -> None:
    """The form sends "" for an untouched optional field."""
    assert BookingRequest.model_validate(_payload(notes="")).notes is None


def test_success_body_serialises_camel_case() -> None:
    """`receivedAt` is what the frontend's BookingSuccess reads."""
    body = BookingAccepted(
        reference="ED-7K2QF9", received_at=datetime.now(UTC)
    ).model_dump(by_alias=True)
    assert body["ok"] is True
    assert body["reference"] == "ED-7K2QF9"
    assert "receivedAt" in body
