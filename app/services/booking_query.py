"""Reading bookings for the console: filters, search, pagination, metrics.

Kept apart from `app.services.booking_status`, which writes. The split is not
ceremony: this module must never mutate, and having it in its own file makes
that checkable at a glance.

Every query is bounded. There is no "fetch all bookings" path, because the one
thing guaranteed about a table like this is that it grows, and an unpaginated
list endpoint is a slow-motion outage.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.booking_status import OPEN_STATUSES, BookingStatus
from app.models.booking import Booking
from app.models.booking_event import BookingEvent, BookingEventType
from app.models.driver import Driver

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

#: How many recent bookings the overview screen shows.
RECENT_LIMIT = 5


@dataclass(frozen=True, slots=True)
class BookingFilters:
    """Everything the console can narrow the list by."""

    statuses: tuple[BookingStatus, ...] = ()
    trip_types: tuple[str, ...] = ()
    #: Filters on `start_date`, not `created_at` — staff think in trip dates.
    starts_from: date | None = None
    starts_to: date | None = None
    #: Free text across reference, name, phone and email.
    query: str | None = None
    assigned: bool | None = None


@dataclass(frozen=True, slots=True)
class Page:
    """One page of results, plus what the pager needs to render itself."""

    items: list[Booking] = field(default_factory=list)
    total: int = 0
    page: int = 1
    limit: int = DEFAULT_PAGE_SIZE

    @property
    def pages(self) -> int:
        """Total page count, at least 1 so an empty list still renders."""
        if self.limit <= 0:
            return 1
        return max(1, -(-self.total // self.limit))


def _apply_filters(statement: Select[Any], filters: BookingFilters) -> Select[Any]:
    """Narrow a select by every filter that was actually supplied."""
    if filters.statuses:
        statement = statement.where(
            Booking.status.in_([status.value for status in filters.statuses])
        )

    if filters.trip_types:
        statement = statement.where(Booking.trip_type.in_(filters.trip_types))

    if filters.starts_from is not None:
        statement = statement.where(Booking.start_date >= filters.starts_from)

    if filters.starts_to is not None:
        statement = statement.where(Booking.start_date <= filters.starts_to)

    if filters.assigned is True:
        statement = statement.where(Booking.assigned_driver_id.is_not(None))
    elif filters.assigned is False:
        statement = statement.where(Booking.assigned_driver_id.is_(None))

    if filters.query:
        term = filters.query.strip()
        if term:
            # ILIKE, not a full-text index: this table is thousands of rows,
            # not millions, and an index that has to be kept in sync is not
            # worth it until a query is actually slow. `escape` stops a
            # customer's own % or _ turning into a wildcard.
            pattern = f"%{_escape_like(term)}%"
            statement = statement.where(
                or_(
                    Booking.reference.ilike(pattern, escape="\\"),
                    Booking.full_name.ilike(pattern, escape="\\"),
                    Booking.phone.ilike(pattern, escape="\\"),
                    Booking.email.ilike(pattern, escape="\\"),
                )
            )

    return statement


def _escape_like(term: str) -> str:
    """Neutralise LIKE wildcards in user input."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_bookings(
    session: AsyncSession,
    *,
    filters: BookingFilters | None = None,
    page: int = 1,
    limit: int = DEFAULT_PAGE_SIZE,
) -> Page:
    """Return one page of bookings, newest first, with the unpaged total.

    `limit` is clamped rather than rejected: a client asking for 10,000 rows
    gets 100 and a working screen, not a 422 it has to handle.
    """
    filters = filters or BookingFilters()
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    page = max(1, page)

    total = await session.scalar(
        _apply_filters(select(func.count()).select_from(Booking), filters)
    )

    rows = await session.scalars(
        _apply_filters(select(Booking), filters)
        # `id` breaks ties: two bookings created in the same millisecond would
        # otherwise be free to swap places between pages, so one could appear
        # twice and another never.
        .order_by(Booking.created_at.desc(), Booking.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )

    return Page(items=list(rows.all()), total=int(total or 0), page=page, limit=limit)


async def drivers_for(
    session: AsyncSession, bookings: list[Booking]
) -> dict[Any, Driver]:
    """Load the drivers referenced by a page of bookings, in one query.

    The alternative is a lazy relationship load per row, which is the N+1 that
    makes a 25-row table issue 26 round trips to Neon.
    """
    ids = {booking.assigned_driver_id for booking in bookings}
    ids.discard(None)
    if not ids:
        return {}

    rows = await session.scalars(select(Driver).where(Driver.id.in_(ids)))
    return {driver.id: driver for driver in rows.all()}


@dataclass(frozen=True, slots=True)
class Metrics:
    """The overview screen's numbers."""

    by_status: dict[str, int]
    total: int
    today: int
    unassigned: int
    open_bookings: int
    median_hours_to_assign: float | None


async def metrics(session: AsyncSession) -> Metrics:
    """Counts for the overview, computed in the database.

    Deliberately not "fetch every booking and count in Python": that is the
    same unbounded read the list endpoint refuses to do, and it would get
    slower every week.
    """
    status_rows = await session.execute(
        select(Booking.status, func.count()).group_by(Booking.status)
    )
    by_status = {status: int(count) for status, count in status_rows.all()}

    unassigned = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.assigned_driver_id.is_(None),
            Booking.status.in_([status.value for status in OPEN_STATUSES]),
        )
    )

    start_of_today = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(Booking.created_at >= start_of_today)
    )

    return Metrics(
        by_status=by_status,
        total=sum(by_status.values()),
        today=int(today or 0),
        unassigned=int(unassigned or 0),
        open_bookings=sum(
            count
            for status, count in by_status.items()
            if status in {open_status.value for open_status in OPEN_STATUSES}
        ),
        median_hours_to_assign=await _median_hours_to_assign(session),
    )


async def _median_hours_to_assign(session: AsyncSession) -> float | None:
    """Median hours between a booking arriving and a driver being attached.

    The metric the phase exists to move: today it is unmeasurable, because
    nothing records when a driver was assigned. It reads the first
    `driver_assigned` event per booking rather than `updated_at`, which any
    later edit would overwrite.

    Returns None until at least one booking has been assigned, rather than 0,
    which would read as "instant" on the dashboard.
    """
    first_assignment = (
        select(
            BookingEvent.booking_id.label("booking_id"),
            func.min(BookingEvent.created_at).label("assigned_at"),
        )
        .where(BookingEvent.event_type == BookingEventType.DRIVER_ASSIGNED.value)
        .group_by(BookingEvent.booking_id)
        .subquery()
    )

    rows = await session.execute(
        select(Booking.created_at, first_assignment.c.assigned_at).join(
            first_assignment, first_assignment.c.booking_id == Booking.id
        )
    )

    # Computed in Python because the median function differs across Postgres
    # and SQLite, and this is a handful of rows either way. If it ever is not,
    # it becomes `percentile_cont` and a Postgres-only integration test.
    hours = sorted(
        (assigned - created).total_seconds() / 3600
        for created, assigned in rows.all()
        if assigned is not None and created is not None
    )
    if not hours:
        return None

    middle = len(hours) // 2
    if len(hours) % 2:
        return float(round(hours[middle], 2))
    return float(round((hours[middle - 1] + hours[middle]) / 2, 2))
