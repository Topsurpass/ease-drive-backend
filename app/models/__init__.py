"""Persistence models.

SQLAlchemy table classes only, never Pydantic API schemas, which live in
``app.schemas``. Every model must be imported here so ``Base.metadata`` is
complete when Alembic autogenerates a migration.
"""

from app.models.booking import Booking
from app.models.booking_event import BookingEvent, BookingEventType
from app.models.driver import Driver
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = [
    "Booking",
    "BookingEvent",
    "BookingEventType",
    "Driver",
    "RefreshToken",
    "User",
]
