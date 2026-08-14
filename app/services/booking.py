"""Business logic for bookings. Knows nothing about HTTP."""

import secrets
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.schemas.booking import BookingRequest

# Crockford-ish base32, copied from the frontend's mock transport: no I, L, O
# or U, so a reference read aloud over the phone cannot be misheard.
REFERENCE_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
REFERENCE_LENGTH: Final[int] = 6
REFERENCE_PREFIX: Final[str] = "ED-"

# 32**6 is ~1.07e9, so a clash is rare but not impossible. Retrying a handful
# of times costs nothing and turns a 500 into a non-event.
MAX_REFERENCE_ATTEMPTS: Final[int] = 5


def create_booking_reference() -> str:
    """Return a human-quotable reference, e.g. ``ED-7K2QF9``."""
    suffix = "".join(
        secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH)
    )
    return f"{REFERENCE_PREFIX}{suffix}"


def _to_model(request: BookingRequest, reference: str) -> Booking:
    return Booking(
        reference=reference,
        full_name=request.full_name,
        phone=request.phone,
        email=str(request.email),
        trip_type=request.trip_type,
        pickup_location=request.pickup_location,
        destination=request.destination,
        start_date=request.start_date,
        duration_days=request.duration_days,
        passengers=request.passengers,
        notes=request.notes,
    )


class ReferenceCollisionError(RuntimeError):
    """Raised when a unique reference could not be generated."""


async def create_booking(session: AsyncSession, request: BookingRequest) -> Booking:
    """Persist a booking and return it, reference included.

    Retries on a unique-constraint violation so a reference collision does not
    reach the caller as a 500.
    """
    for _ in range(MAX_REFERENCE_ATTEMPTS):
        booking = _to_model(request, create_booking_reference())
        session.add(booking)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            continue
        await session.refresh(booking)
        return booking

    raise ReferenceCollisionError(
        f"could not generate a unique reference in {MAX_REFERENCE_ATTEMPTS} attempts"
    )
