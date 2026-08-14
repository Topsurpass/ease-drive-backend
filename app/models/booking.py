"""Booking table.

Tables are prefixed `ease_` because this Neon database is shared with the
nibbs-report app, whose tables carry a `nibbs_` prefix. Same convention, same
reason: two apps on one database must not collide.

Column limits mirror `ease-drive-frontend/src/lib/validators/booking.schema.ts`
exactly. The check constraints are deliberate duplication: the API rejects bad
input first, but a future script or psql session writing directly cannot store
a row the frontend would refuse to render.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Index,
    SmallInteger,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MAX_NAME = 80
MAX_PHONE = 20
MAX_EMAIL = 254
MAX_TRIP_TYPE = 32
MAX_LOCATION = 120
MAX_NOTES = 500
MAX_REFERENCE = 16


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Booking(Base):
    """A driver booking request submitted from the marketing site."""

    __tablename__ = "ease_bookings"
    __table_args__ = (
        CheckConstraint(
            "duration_days >= 1 AND duration_days <= 30",
            name="ease_bookings_duration_days_range",
        ),
        CheckConstraint(
            "passengers >= 1 AND passengers <= 14",
            name="ease_bookings_passengers_range",
        ),
        Index("ease_bookings_created_at_idx", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Generated in the service, not the database, so the value is known before
    # the INSERT and can be returned even if the caller never re-reads the row.
    reference: Mapped[str] = mapped_column(
        String(MAX_REFERENCE), unique=True, index=True
    )

    full_name: Mapped[str] = mapped_column(String(MAX_NAME))
    phone: Mapped[str] = mapped_column(String(MAX_PHONE))
    email: Mapped[str] = mapped_column(String(MAX_EMAIL), index=True)
    trip_type: Mapped[str] = mapped_column(String(MAX_TRIP_TYPE))
    pickup_location: Mapped[str] = mapped_column(String(MAX_LOCATION))
    destination: Mapped[str] = mapped_column(String(MAX_LOCATION))

    # A calendar date, not a timestamp: a trip start has no time zone, and
    # storing it as one would shift the day for anyone east or west of the
    # server. Same reasoning as the frontend's ISO `yyyy-MM-dd` string.
    start_date: Mapped[date] = mapped_column(Date)

    duration_days: Mapped[int] = mapped_column(SmallInteger)
    passengers: Mapped[int] = mapped_column(SmallInteger)
    notes: Mapped[str | None] = mapped_column(String(MAX_NOTES), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    def __repr__(self) -> str:
        return f"<Booking {self.reference} {self.trip_type} {self.start_date}>"
