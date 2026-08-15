"""Audit trail for everything that happens to a booking.

Append-only: rows are inserted, never updated or deleted. That is what makes it
evidence. The booking row carries the *current* status; this table carries how
it got there and who moved it.

It also records reads. `booking_viewed` exists because the detail endpoint is
the only place a customer's real phone number and email are returned in full —
see the list projection in `app.schemas.admin_booking` — and an unlogged look
at customer PII is exactly the thing you cannot reconstruct afterwards.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MAX_EVENT_TYPE = 32
MAX_STATUS = 16
MAX_NOTE = 500


class BookingEventType(StrEnum):
    """What kind of thing happened."""

    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    DRIVER_ASSIGNED = "driver_assigned"
    DRIVER_UNASSIGNED = "driver_unassigned"
    NOTE_ADDED = "note_added"
    VIEWED = "viewed"


_EVENT_VALUES = ", ".join(f"'{event.value}'" for event in BookingEventType)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class BookingEvent(Base):
    """One recorded action against a booking."""

    __tablename__ = "ease_booking_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({_EVENT_VALUES})",
            name="ease_booking_events_event_type_valid",
        ),
        Index(
            "ease_booking_events_booking_id_created_at_idx",
            "booking_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    booking_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ease_bookings.id", ondelete="CASCADE")
    )

    #: Null for anything the system did with no signed-in person behind it,
    #: such as the booking's own `created` event from the public form.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ease_users.id", ondelete="SET NULL"), nullable=True
    )

    event_type: Mapped[str] = mapped_column(String(MAX_EVENT_TYPE))

    from_status: Mapped[str | None] = mapped_column(String(MAX_STATUS), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(MAX_STATUS), nullable=True)

    #: Free text for a status-change reason or an internal note. Never customer
    #: PII — the customer's own details already live on the booking row.
    note: Mapped[str | None] = mapped_column(String(MAX_NOTE), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    def __repr__(self) -> str:
        return f"<BookingEvent {self.event_type} {self.created_at:%Y-%m-%d %H:%M}>"
