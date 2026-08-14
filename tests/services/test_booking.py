"""Gate tests for the booking service, against in-memory SQLite."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.schemas.booking import BookingRequest
from app.services.booking import (
    REFERENCE_ALPHABET,
    create_booking,
    create_booking_reference,
)

REFERENCE_RE = re.compile(r"^ED-[0-9A-HJKMNP-TV-Z]{6}$")


def _request(**overrides: Any) -> BookingRequest:
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
    return BookingRequest.model_validate(body)


def test_reference_matches_the_frontend_format() -> None:
    """The success panel displays this string; it must look like ED-XXXXXX."""
    assert REFERENCE_RE.match(create_booking_reference())


def test_reference_alphabet_excludes_confusable_letters() -> None:
    """No I, L, O or U, so a reference read aloud cannot be misheard."""
    assert not set("ILOU") & set(REFERENCE_ALPHABET)


def test_references_are_not_repeated() -> None:
    assert len({create_booking_reference() for _ in range(200)}) == 200


async def test_persists_every_field(db_session: AsyncSession) -> None:
    booking = await create_booking(db_session, _request())

    stored = (
        await db_session.execute(select(Booking).where(Booking.id == booking.id))
    ).scalar_one()

    assert stored.full_name == "Ada Lovelace"
    assert stored.phone == "+234 800 000 0000"
    assert stored.email == "ada@example.com"
    assert stored.trip_type == "airport"
    assert stored.pickup_location == "Ikeja GRA"
    assert stored.destination == "Murtala Muhammed Airport"
    assert stored.duration_days == 2
    assert stored.passengers == 3
    assert stored.notes == "Two large suitcases."


async def test_assigns_a_reference_and_timestamp(db_session: AsyncSession) -> None:
    booking = await create_booking(db_session, _request())
    assert REFERENCE_RE.match(booking.reference)
    assert booking.created_at is not None


async def test_stores_absent_notes_as_null(db_session: AsyncSession) -> None:
    booking = await create_booking(db_session, _request(notes=""))
    assert booking.notes is None


async def test_two_bookings_get_distinct_references(
    db_session: AsyncSession,
) -> None:
    first = await create_booking(db_session, _request())
    second = await create_booking(db_session, _request())
    assert first.reference != second.reference
    assert first.id != second.id


async def test_survives_a_reference_collision(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicate reference must retry, not surface as a 500."""
    first = await create_booking(db_session, _request())

    references = iter([first.reference, "ED-UNIQ01"])
    monkeypatch.setattr(
        "app.services.booking.create_booking_reference", lambda: next(references)
    )

    second = await create_booking(db_session, _request())
    assert second.reference == "ED-UNIQ01"
