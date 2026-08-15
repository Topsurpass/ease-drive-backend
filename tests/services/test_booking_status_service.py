"""Gate tests for moving bookings through the lifecycle.

The transition table itself is covered exhaustively in
`tests/domain/test_booking_status.py`. What is covered here is what happens to
the *database* when a move is applied: that the audit row is written in the same
transaction, that assignment and the status change commit together, and that an
illegal move leaves the booking exactly as it was.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.booking_status import BookingStatus
from app.domain.roles import UserRole
from app.domain.vetting import VettingStatus
from app.models.booking import Booking
from app.models.booking_event import BookingEvent, BookingEventType
from app.models.driver import Driver
from app.models.user import User
from app.services import booking_status
from app.services.auth import create_user
from app.services.booking_status import Actor


@pytest.fixture
async def actor(db_session: AsyncSession) -> Actor:
    user: User = await create_user(
        db_session,
        email="ops@example.com",
        password="a-perfectly-fine-passphrase",
        full_name="Ops Person",
        role=UserRole.OPS.value,
    )
    return Actor(user_id=user.id)


@pytest.fixture
async def booking(db_session: AsyncSession) -> Booking:
    record = Booking(
        reference="ED-TEST01",
        full_name="Ada Lovelace",
        phone="+234 800 000 0000",
        email="ada@example.com",
        trip_type="airport",
        pickup_location="Ikeja GRA",
        destination="Murtala Muhammed Airport",
        start_date=(datetime.now(UTC) + timedelta(days=3)).date(),
        duration_days=2,
        passengers=3,
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


async def _driver(
    session: AsyncSession,
    *,
    vetting: VettingStatus = VettingStatus.VERIFIED,
    active: bool = True,
) -> Driver:
    record = Driver(
        full_name="Grace Hopper",
        phone="+234 801 111 1111",
        vetting_status=vetting.value,
        is_active=active,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def _events(session: AsyncSession, booking: Booking) -> list[BookingEvent]:
    rows = await session.scalars(
        select(BookingEvent)
        .where(BookingEvent.booking_id == booking.id)
        .order_by(BookingEvent.created_at, BookingEvent.id)
    )
    return list(rows.all())


# --- lookup -----------------------------------------------------------------


async def test_a_new_booking_starts_as_new(booking: Booking) -> None:
    assert booking.status == BookingStatus.NEW.value


@pytest.mark.parametrize("given", ["ED-TEST01", "ed-test01", " ed-Test01 "])
async def test_reference_lookup_ignores_case_and_padding(
    db_session: AsyncSession, booking: Booking, given: str
) -> None:
    """Staff read references off phone calls, where case survives nothing."""
    found = await booking_status.get_by_reference(db_session, given)
    assert found.id == booking.id


async def test_an_unknown_reference_raises(db_session: AsyncSession) -> None:
    with pytest.raises(booking_status.BookingNotFoundError):
        await booking_status.get_by_reference(db_session, "ED-NOPE00")


# --- status changes ---------------------------------------------------------


async def test_a_legal_transition_is_applied(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    updated = await booking_status.change_status(
        db_session, booking, BookingStatus.CANCELLED, actor
    )
    assert updated.status == BookingStatus.CANCELLED.value


async def test_a_status_change_writes_an_audit_row(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """The trail is the point: who moved it, from what, to what."""
    await booking_status.change_status(
        db_session, booking, BookingStatus.CANCELLED, actor, note="Customer rang off."
    )

    events = await _events(db_session, booking)
    assert len(events) == 1
    assert events[0].event_type == BookingEventType.STATUS_CHANGED.value
    assert events[0].from_status == BookingStatus.NEW.value
    assert events[0].to_status == BookingStatus.CANCELLED.value
    assert events[0].note == "Customer rang off."
    assert events[0].actor_user_id == actor.user_id


async def test_an_illegal_transition_raises_and_changes_nothing(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """A rejected move must not leave a half-applied change or a stray event."""
    with pytest.raises(booking_status.IllegalTransitionError):
        await booking_status.change_status(
            db_session, booking, BookingStatus.COMPLETED, actor
        )

    await db_session.refresh(booking)
    assert booking.status == BookingStatus.NEW.value
    assert await _events(db_session, booking) == []


async def test_the_illegal_transition_error_names_both_ends(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """The endpoint turns these into a message a human can act on."""
    with pytest.raises(booking_status.IllegalTransitionError) as caught:
        await booking_status.change_status(
            db_session, booking, BookingStatus.COMPLETED, actor
        )

    assert caught.value.current is BookingStatus.NEW
    assert caught.value.target is BookingStatus.COMPLETED


async def test_a_terminal_booking_cannot_be_moved(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    await booking_status.change_status(
        db_session, booking, BookingStatus.CANCELLED, actor
    )

    with pytest.raises(booking_status.IllegalTransitionError):
        await booking_status.change_status(
            db_session, booking, BookingStatus.NEW, actor
        )


async def test_the_full_happy_path_walks_end_to_end(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """new -> matched -> confirmed -> in_progress -> completed."""
    driver = await _driver(db_session)
    await booking_status.assign_driver(db_session, booking, driver.id, actor)

    for target in (
        BookingStatus.CONFIRMED,
        BookingStatus.IN_PROGRESS,
        BookingStatus.COMPLETED,
    ):
        await booking_status.change_status(db_session, booking, target, actor)

    await db_session.refresh(booking)
    assert booking.status == BookingStatus.COMPLETED.value

    recorded = [
        event.to_status
        for event in await _events(db_session, booking)
        if event.event_type == BookingEventType.STATUS_CHANGED.value
    ]
    assert recorded == ["matched", "confirmed", "in_progress", "completed"]


# --- assignment -------------------------------------------------------------


async def test_assigning_a_driver_also_matches_the_booking(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """One business event, one transaction. A driver on a `new` booking is a bug."""
    driver = await _driver(db_session)

    updated = await booking_status.assign_driver(db_session, booking, driver.id, actor)

    assert updated.assigned_driver_id == driver.id
    assert updated.status == BookingStatus.MATCHED.value


async def test_assignment_records_both_events(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    driver = await _driver(db_session)
    await booking_status.assign_driver(db_session, booking, driver.id, actor)

    kinds = [event.event_type for event in await _events(db_session, booking)]
    assert BookingEventType.DRIVER_ASSIGNED.value in kinds
    assert BookingEventType.STATUS_CHANGED.value in kinds


async def test_an_unvetted_driver_cannot_be_assigned(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """ "Vetted drivers" is the promise on the landing page. This enforces it."""
    driver = await _driver(db_session, vetting=VettingStatus.PENDING)

    with pytest.raises(booking_status.DriverNotAssignableError):
        await booking_status.assign_driver(db_session, booking, driver.id, actor)

    await db_session.refresh(booking)
    assert booking.assigned_driver_id is None
    assert booking.status == BookingStatus.NEW.value


async def test_an_inactive_driver_cannot_be_assigned(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """Vetted but not working today is still not assignable."""
    driver = await _driver(db_session, active=False)

    with pytest.raises(booking_status.DriverNotAssignableError):
        await booking_status.assign_driver(db_session, booking, driver.id, actor)


async def test_a_rejected_driver_cannot_be_assigned(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    driver = await _driver(db_session, vetting=VettingStatus.REJECTED)

    with pytest.raises(booking_status.DriverNotAssignableError):
        await booking_status.assign_driver(db_session, booking, driver.id, actor)


async def test_assigning_an_unknown_driver_raises(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    with pytest.raises(booking_status.DriverNotFoundError):
        await booking_status.assign_driver(db_session, booking, uuid.uuid4(), actor)


async def test_reassigning_on_a_confirmed_booking_keeps_its_status(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """Swapping a driver on a confirmed trip must not walk it back to matched."""
    first = await _driver(db_session)
    await booking_status.assign_driver(db_session, booking, first.id, actor)
    await booking_status.change_status(
        db_session, booking, BookingStatus.CONFIRMED, actor
    )

    second = Driver(
        full_name="Katherine Johnson",
        phone="+234 802 222 2222",
        vetting_status=VettingStatus.VERIFIED.value,
    )
    db_session.add(second)
    await db_session.commit()
    await db_session.refresh(second)

    updated = await booking_status.assign_driver(db_session, booking, second.id, actor)

    assert updated.assigned_driver_id == second.id
    assert updated.status == BookingStatus.CONFIRMED.value


# --- unassignment -----------------------------------------------------------


async def test_unassigning_returns_a_matched_booking_to_the_queue(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    driver = await _driver(db_session)
    await booking_status.assign_driver(db_session, booking, driver.id, actor)

    updated = await booking_status.unassign_driver(db_session, booking, actor)

    assert updated.assigned_driver_id is None
    assert updated.status == BookingStatus.NEW.value


async def test_unassigning_a_booking_with_no_driver_is_a_no_op(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    await booking_status.unassign_driver(db_session, booking, actor)
    assert await _events(db_session, booking) == []


# --- notes and views --------------------------------------------------------


async def test_internal_notes_are_saved_and_recorded(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    updated = await booking_status.set_internal_notes(
        db_session, booking, "  Customer prefers an estate car.  ", actor
    )

    assert updated.internal_notes == "Customer prefers an estate car."
    kinds = [event.event_type for event in await _events(db_session, booking)]
    assert BookingEventType.NOTE_ADDED.value in kinds


async def test_blank_internal_notes_are_stored_as_null(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """An empty string and "no note" must not be two different states."""
    updated = await booking_status.set_internal_notes(db_session, booking, "   ", actor)
    assert updated.internal_notes is None


async def test_viewing_a_booking_is_recorded(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    """The detail endpoint is the only place real contact details are returned."""
    await booking_status.record_view(db_session, booking, actor)

    events = await _events(db_session, booking)
    assert [event.event_type for event in events] == [BookingEventType.VIEWED.value]
    assert events[0].actor_user_id == actor.user_id


async def test_the_timeline_is_oldest_first(
    db_session: AsyncSession, booking: Booking, actor: Actor
) -> None:
    driver = await _driver(db_session)
    await booking_status.assign_driver(db_session, booking, driver.id, actor)
    await booking_status.change_status(
        db_session, booking, BookingStatus.CONFIRMED, actor
    )

    events = await booking_status.timeline(db_session, booking)
    timestamps = [event.created_at for event in events]
    assert timestamps == sorted(timestamps)
