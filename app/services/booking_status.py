"""Moving a booking through its lifecycle, and recording who moved it.

Every mutation here writes an `ease_booking_events` row in the same
transaction as the change itself. That is deliberate: an audit trail written
afterwards, or best-effort, is one that silently goes missing exactly when
something has gone wrong and you need it.

The legal moves live in `app.domain.booking_status`, not here. This module
enforces them; it does not restate them.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.booking_status import BookingStatus, can_transition
from app.domain.vetting import VettingStatus, is_assignable
from app.models.booking import Booking
from app.models.booking_event import BookingEvent, BookingEventType
from app.models.driver import Driver


class BookingError(Exception):
    """Base for anything that stops a booking being changed."""


class BookingNotFoundError(BookingError):
    """No booking with that reference."""


class IllegalTransitionError(BookingError):
    """The requested status cannot be reached from the current one."""

    def __init__(self, current: BookingStatus, target: BookingStatus) -> None:
        super().__init__(f"cannot move a booking from {current} to {target}")
        self.current = current
        self.target = target


class DriverNotFoundError(BookingError):
    """No driver with that id."""


class DriverNotAssignableError(BookingError):
    """The driver exists but is not vetted, or is not currently active."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is making the change. `None` id means the system, not a person."""

    user_id: uuid.UUID | None


async def get_by_reference(session: AsyncSession, reference: str) -> Booking:
    """Load a booking or raise. References are matched case-insensitively.

    Staff read references off phone calls and emails, where case survives
    nothing. `ED-7k2qf9` and `ED-7K2QF9` are the same booking.
    """
    booking = await session.scalar(
        select(Booking).where(Booking.reference == reference.strip().upper())
    )
    if booking is None:
        raise BookingNotFoundError(reference)
    return booking


def _record(
    session: AsyncSession,
    booking: Booking,
    event_type: BookingEventType,
    actor: Actor,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
) -> None:
    """Append an audit row. Flushed with the caller's transaction, not alone."""
    session.add(
        BookingEvent(
            booking_id=booking.id,
            actor_user_id=actor.user_id,
            event_type=event_type.value,
            from_status=from_status,
            to_status=to_status,
            note=note,
        )
    )


async def record_view(session: AsyncSession, booking: Booking, actor: Actor) -> None:
    """Log that someone looked at a booking's full contact details.

    The list view masks phone and email; the detail view does not. This is what
    makes "who has seen this customer's number" an answerable question rather
    than an unknowable one.
    """
    _record(session, booking, BookingEventType.VIEWED, actor)
    await session.commit()


async def change_status(
    session: AsyncSession,
    booking: Booking,
    target: BookingStatus,
    actor: Actor,
    *,
    note: str | None = None,
) -> Booking:
    """Move a booking to `target`, or raise `IllegalTransitionError`.

    Rejecting a no-op move (`current == target`) is deliberate: allowing it
    would write an audit event recording a change that did not happen.
    """
    current = BookingStatus(booking.status)
    if not can_transition(current, target):
        raise IllegalTransitionError(current, target)

    booking.status = target.value
    _record(
        session,
        booking,
        BookingEventType.STATUS_CHANGED,
        actor,
        from_status=current.value,
        to_status=target.value,
        note=note,
    )

    await session.commit()
    await session.refresh(booking)
    return booking


async def assign_driver(
    session: AsyncSession, booking: Booking, driver_id: uuid.UUID, actor: Actor
) -> Booking:
    """Attach a driver and move the booking to `matched`, in one transaction.

    Assignment and the status move are the same business event, so they commit
    together or not at all. Splitting them would allow a booking with a driver
    but still sitting in the `new` queue, which is the kind of state nobody
    thinks to look for until a customer calls.

    A booking already past `matched` keeps its status: re-assigning a driver to
    a confirmed trip is a legitimate swap, not a reason to walk it backwards.
    """
    driver = await session.get(Driver, driver_id)
    if driver is None:
        raise DriverNotFoundError(str(driver_id))

    if not is_assignable(VettingStatus(driver.vetting_status), driver.is_active):
        raise DriverNotAssignableError(
            "This driver has not passed vetting."
            if driver.vetting_status != VettingStatus.VERIFIED.value
            else "This driver is not currently active."
        )

    current = BookingStatus(booking.status)
    previous_driver_id = booking.assigned_driver_id
    booking.assigned_driver_id = driver.id

    _record(
        session,
        booking,
        BookingEventType.DRIVER_ASSIGNED,
        actor,
        note=(
            f"Reassigned from another driver to {driver.full_name}."
            if previous_driver_id and previous_driver_id != driver.id
            else f"Assigned to {driver.full_name}."
        ),
    )

    if can_transition(current, BookingStatus.MATCHED):
        booking.status = BookingStatus.MATCHED.value
        _record(
            session,
            booking,
            BookingEventType.STATUS_CHANGED,
            actor,
            from_status=current.value,
            to_status=BookingStatus.MATCHED.value,
        )

    await session.commit()
    await session.refresh(booking)
    return booking


async def unassign_driver(
    session: AsyncSession, booking: Booking, actor: Actor
) -> Booking:
    """Detach the driver and return the booking to the queue.

    Only from `matched`: once a trip is confirmed or under way, removing the
    driver without a status decision would leave a live booking nobody owns.
    """
    if booking.assigned_driver_id is None:
        return booking

    current = BookingStatus(booking.status)
    booking.assigned_driver_id = None
    _record(session, booking, BookingEventType.DRIVER_UNASSIGNED, actor)

    if current is BookingStatus.MATCHED:
        booking.status = BookingStatus.NEW.value
        _record(
            session,
            booking,
            BookingEventType.STATUS_CHANGED,
            actor,
            from_status=current.value,
            to_status=BookingStatus.NEW.value,
        )

    await session.commit()
    await session.refresh(booking)
    return booking


async def set_internal_notes(
    session: AsyncSession, booking: Booking, notes: str | None, actor: Actor
) -> Booking:
    """Replace the staff-only notes on a booking."""
    booking.internal_notes = notes.strip() if notes and notes.strip() else None
    _record(session, booking, BookingEventType.NOTE_ADDED, actor)

    await session.commit()
    await session.refresh(booking)
    return booking


async def timeline(session: AsyncSession, booking: Booking) -> list[BookingEvent]:
    """Every recorded event for a booking, oldest first."""
    result = await session.scalars(
        select(BookingEvent)
        .where(BookingEvent.booking_id == booking.id)
        .order_by(BookingEvent.created_at, BookingEvent.id)
    )
    return list(result.all())
