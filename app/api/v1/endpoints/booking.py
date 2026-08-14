"""Booking submission endpoint."""

from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.schemas.booking import BookingAccepted, BookingError, BookingRequest
from app.services.booking import create_booking

router = APIRouter(tags=["bookings"])


@router.post(
    "/bookings",
    response_model=BookingAccepted,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a booking request",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": BookingError},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BookingError},
    },
)
async def submit_booking(
    request: BookingRequest, session: SessionDep
) -> BookingAccepted:
    """Persist a booking request and return its reference."""
    booking = await create_booking(session, request)
    return BookingAccepted(
        reference=booking.reference,
        received_at=booking.created_at,
    )
