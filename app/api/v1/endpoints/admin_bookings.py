"""The ops console's booking endpoints.

Every route here requires a signed-in staff member. Authorization is a
dependency, not a check inside the handler, so a new route cannot be added
without one — forgetting `CurrentUserDep` means the function has no session to
work with and fails immediately, rather than quietly serving customer data to
anyone who asks.
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUserDep, SessionDep
from app.domain.booking_status import (
    STATUS_LABELS,
    BookingStatus,
    allowed_transitions,
    is_terminal,
)
from app.models.booking import Booking
from app.models.booking_event import BookingEvent
from app.models.user import User
from app.schemas.admin_booking import (
    AdminError,
    AssignDriverRequest,
    BookingDetail,
    BookingListItem,
    BookingPage,
    BookingStatusOption,
    DriverSummary,
    InternalNotesRequest,
    MetricsResponse,
    StatusChangeRequest,
    TimelineEvent,
)
from app.services import booking_query, booking_status
from app.services.booking_status import Actor

router = APIRouter(prefix="/admin", tags=["ops console"])

_Responses = dict[int | str, dict[str, Any]]
_NOT_FOUND: _Responses = {status.HTTP_404_NOT_FOUND: {"model": AdminError}}
_CONFLICT: _Responses = {status.HTTP_409_CONFLICT: {"model": AdminError}}
_UNPROCESSABLE: _Responses = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AdminError}
}


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=AdminError(code=code, message=message).model_dump(  # type: ignore[arg-type]
            by_alias=True, mode="json"
        ),
    )


async def _load(session: SessionDep, reference: str) -> Booking:
    try:
        return await booking_status.get_by_reference(session, reference)
    except booking_status.BookingNotFoundError as error:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            f"No booking with reference {reference}.",
        ) from error


async def _detail(session: SessionDep, booking: Booking) -> BookingDetail:
    """Build the full response for one booking, timeline included.

    The timeline is read once and the actors resolved from it in a single
    follow-up query, rather than each helper fetching its own copy.
    """
    driver = None
    if booking.assigned_driver_id is not None:
        drivers = await booking_query.drivers_for(session, [booking])
        driver = drivers.get(booking.assigned_driver_id)

    events = await booking_status.timeline(session, booking)
    actor_names = await _actor_names(session, events)
    current = BookingStatus(booking.status)

    return BookingDetail(
        reference=booking.reference,
        status=current,
        trip_type=booking.trip_type,
        full_name=booking.full_name,
        phone=booking.phone,
        email=booking.email,
        pickup_location=booking.pickup_location,
        destination=booking.destination,
        start_date=booking.start_date,
        duration_days=booking.duration_days,
        passengers=booking.passengers,
        notes=booking.notes,
        driver=DriverSummary.of(driver),
        internal_notes=booking.internal_notes,
        allowed_transitions=list(allowed_transitions(current)),
        timeline=[
            TimelineEvent.of(
                event,
                actor_names.get(event.actor_user_id) if event.actor_user_id else None,
            )
            for event in events
        ],
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


async def _actor_names(
    session: SessionDep, events: list[BookingEvent]
) -> dict[uuid.UUID, str]:
    """Map actor ids to names for a set of events, in one query.

    One query for the whole timeline rather than a lazy load per event, which
    on a busy booking is a dozen round trips to Neon to render one panel.
    """
    ids = {event.actor_user_id for event in events if event.actor_user_id}
    if not ids:
        return {}

    rows = await session.scalars(select(User).where(User.id.in_(ids)))
    return {user.id: user.full_name for user in rows.all()}


@router.get(
    "/booking-statuses",
    response_model=list[BookingStatusOption],
    summary="The booking lifecycle and its legal transitions",
)
async def booking_statuses(_: CurrentUserDep) -> list[BookingStatusOption]:
    """Publish the transition table.

    The single source of truth lives in `app.domain.booking_status`. The
    frontend generates its copy from this response rather than maintaining a
    second one, so the two cannot drift.
    """
    return [
        BookingStatusOption(
            id=member,
            label=STATUS_LABELS[member],
            allowed_transitions=list(allowed_transitions(member)),
            is_terminal=is_terminal(member),
        )
        for member in BookingStatus
    ]


@router.get(
    "/bookings",
    response_model=BookingPage,
    summary="List bookings (contact details masked)",
)
async def list_bookings(
    session: SessionDep,
    _: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=booking_query.MAX_PAGE_SIZE)] = (
        booking_query.DEFAULT_PAGE_SIZE
    ),
    booking_status_filter: Annotated[
        list[BookingStatus] | None, Query(alias="status")
    ] = None,
    trip_type: Annotated[list[str] | None, Query()] = None,
    starts_from: Annotated[date | None, Query(alias="from")] = None,
    starts_to: Annotated[date | None, Query(alias="to")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    assigned: Annotated[bool | None, Query()] = None,
) -> BookingPage:
    """One page of bookings, newest first.

    Phone and email come back masked and the customer's note is omitted. The
    full record is one call away, at `/admin/bookings/{reference}`, which logs
    the look.
    """
    result = await booking_query.list_bookings(
        session,
        filters=booking_query.BookingFilters(
            statuses=tuple(booking_status_filter or ()),
            trip_types=tuple(trip_type or ()),
            starts_from=starts_from,
            starts_to=starts_to,
            query=q,
            assigned=assigned,
        ),
        page=page,
        limit=limit,
    )

    drivers = await booking_query.drivers_for(session, result.items)
    return BookingPage(
        items=[
            BookingListItem.of(booking, drivers.get(booking.assigned_driver_id))
            for booking in result.items
        ],
        total=result.total,
        page=result.page,
        limit=result.limit,
        pages=result.pages,
    )


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    summary="Overview counts",
)
async def metrics(session: SessionDep, _: CurrentUserDep) -> MetricsResponse:
    """Counts for the dashboard, computed in the database."""
    numbers = await booking_query.metrics(session)
    recent = await booking_query.list_bookings(
        session, page=1, limit=booking_query.RECENT_LIMIT
    )
    drivers = await booking_query.drivers_for(session, recent.items)

    return MetricsResponse(
        by_status=numbers.by_status,
        total=numbers.total,
        today=numbers.today,
        unassigned=numbers.unassigned,
        open_bookings=numbers.open_bookings,
        median_hours_to_assign=numbers.median_hours_to_assign,
        recent=[
            BookingListItem.of(booking, drivers.get(booking.assigned_driver_id))
            for booking in recent.items
        ],
    )


@router.get(
    "/bookings/{reference}",
    response_model=BookingDetail,
    summary="One booking in full (audited)",
    responses=_NOT_FOUND,
)
async def get_booking(
    reference: str, session: SessionDep, user: CurrentUserDep
) -> BookingDetail:
    """Return the full record, and record that it was looked at.

    This is the only endpoint that returns a customer's real phone number and
    email, so it is the one that has to leave a trace.
    """
    booking = await _load(session, reference)
    await booking_status.record_view(session, booking, Actor(user_id=user.id))

    return await _detail(session, booking)


@router.patch(
    "/bookings/{reference}/status",
    response_model=BookingDetail,
    summary="Move a booking to another status",
    responses={**_NOT_FOUND, **_CONFLICT},
)
async def change_status(
    reference: str,
    request: StatusChangeRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> BookingDetail:
    """Apply a status transition, or 409 if it is not a legal move."""
    booking = await _load(session, reference)

    try:
        booking = await booking_status.change_status(
            session, booking, request.status, Actor(user_id=user.id), note=request.note
        )
    except booking_status.IllegalTransitionError as error:
        # 409, not 422: the request is well formed, it conflicts with the
        # booking's current state. The console uses that difference to decide
        # between "fix your input" and "this booking moved under you".
        # Built from the labels rather than the raw enum values, and not
        # lower-cased: "cannot become new request" reads like broken English,
        # whereas the label as written reads as the name of a state.
        raise _error(
            status.HTTP_409_CONFLICT,
            "illegal_transition",
            f'A booking marked "{STATUS_LABELS[error.current]}" cannot be moved '
            f'to "{STATUS_LABELS[error.target]}".',
        ) from error

    return await _detail(session, booking)


@router.post(
    "/bookings/{reference}/assign",
    response_model=BookingDetail,
    summary="Assign a driver",
    responses={**_NOT_FOUND, **_UNPROCESSABLE},
)
async def assign_driver(
    reference: str,
    request: AssignDriverRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> BookingDetail:
    """Attach a driver and move the booking to `matched`, atomically."""
    booking = await _load(session, reference)

    try:
        driver_id = uuid.UUID(request.driver_id)
    except ValueError as error:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_error",
            "That is not a valid driver id.",
        ) from error

    try:
        booking = await booking_status.assign_driver(
            session, booking, driver_id, Actor(user_id=user.id)
        )
    except booking_status.DriverNotFoundError as error:
        raise _error(
            status.HTTP_404_NOT_FOUND, "not_found", "No driver with that id."
        ) from error
    except booking_status.DriverNotAssignableError as error:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "driver_not_assignable",
            error.reason,
        ) from error

    return await _detail(session, booking)


@router.post(
    "/bookings/{reference}/unassign",
    response_model=BookingDetail,
    summary="Remove the assigned driver",
    responses=_NOT_FOUND,
)
async def unassign_driver(
    reference: str, session: SessionDep, user: CurrentUserDep
) -> BookingDetail:
    """Detach the driver, returning a matched booking to the queue."""
    booking = await _load(session, reference)
    booking = await booking_status.unassign_driver(
        session, booking, Actor(user_id=user.id)
    )

    return await _detail(session, booking)


@router.patch(
    "/bookings/{reference}/notes",
    response_model=BookingDetail,
    summary="Replace the internal notes",
    responses=_NOT_FOUND,
)
async def set_internal_notes(
    reference: str,
    request: InternalNotesRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> BookingDetail:
    """Set the staff-only notes on a booking."""
    booking = await _load(session, reference)
    booking = await booking_status.set_internal_notes(
        session, booking, request.internal_notes, Actor(user_id=user.id)
    )

    return await _detail(session, booking)
