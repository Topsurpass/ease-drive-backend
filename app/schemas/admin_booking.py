"""Wire contract for the ops console's booking screens.

Two shapes on purpose, and the difference is the point:

``BookingListItem``
    What the table shows. Phone and email are masked, name is trimmed to
    "Ada L.", and internal notes and the customer's own notes are absent
    entirely. One request returns 25 customers' details, and almost none of
    those requests need them.

``BookingDetail``
    The full record, returned only when someone opens one booking — and the
    endpoint writes a `viewed` audit row when it does.

Splitting them means the widest surface carries the least, and looking at real
contact details is a deliberate, logged act rather than a side effect of
loading a list.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.domain.booking_status import BookingStatus
from app.domain.masking import mask_email, mask_name, mask_phone
from app.models.booking import Booking
from app.models.booking_event import BookingEvent
from app.models.driver import Driver

MAX_INTERNAL_NOTES = 2000
MAX_STATUS_NOTE = 500


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, str_strip_whitespace=True
    )


class DriverSummary(CamelModel):
    """Just enough of a driver to name them on a booking."""

    id: str
    full_name: str
    phone: str
    vehicle_plate: str | None = None

    @classmethod
    def of(cls, driver: Driver | None) -> "DriverSummary | None":
        if driver is None:
            return None
        return cls(
            id=str(driver.id),
            full_name=driver.full_name,
            phone=driver.phone,
            vehicle_plate=driver.vehicle_plate,
        )


class BookingListItem(CamelModel):
    """One row of the bookings table. Contact details are masked."""

    reference: str
    status: BookingStatus
    trip_type: str

    #: Trimmed, not hidden: staff need to talk about the booking, and a first
    #: name is not a contact route.
    customer_name: str
    #: Masked. Enough to recognise a record, not enough to use one.
    phone_masked: str
    email_masked: str

    pickup_location: str
    destination: str
    start_date: date
    duration_days: int
    passengers: int

    driver: DriverSummary | None = None
    has_notes: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, booking: Booking, driver: Driver | None = None) -> "BookingListItem":
        return cls(
            reference=booking.reference,
            status=BookingStatus(booking.status),
            trip_type=booking.trip_type,
            customer_name=mask_name(booking.full_name),
            phone_masked=mask_phone(booking.phone),
            email_masked=mask_email(booking.email),
            pickup_location=booking.pickup_location,
            destination=booking.destination,
            start_date=booking.start_date,
            duration_days=booking.duration_days,
            passengers=booking.passengers,
            driver=DriverSummary.of(driver),
            # Whether a note exists is useful in a table; its contents are not.
            has_notes=bool(booking.notes),
            created_at=booking.created_at,
            updated_at=booking.updated_at,
        )


class BookingPage(CamelModel):
    """A page of bookings plus what the pager needs."""

    items: list[BookingListItem]
    total: int
    page: int
    limit: int
    pages: int


class TimelineEvent(CamelModel):
    """One row of a booking's history."""

    id: str
    event_type: str
    from_status: str | None = None
    to_status: str | None = None
    note: str | None = None
    actor_name: str | None = None
    created_at: datetime

    @classmethod
    def of(cls, event: BookingEvent, actor_name: str | None) -> "TimelineEvent":
        return cls(
            id=str(event.id),
            event_type=event.event_type,
            from_status=event.from_status,
            to_status=event.to_status,
            note=event.note,
            actor_name=actor_name,
            created_at=event.created_at,
        )


class BookingDetail(CamelModel):
    """The full booking. Only ever returned one at a time, and audited."""

    reference: str
    status: BookingStatus
    trip_type: str

    full_name: str
    phone: str
    email: str

    pickup_location: str
    destination: str
    start_date: date
    duration_days: int
    passengers: int
    notes: str | None = None

    driver: DriverSummary | None = None
    internal_notes: str | None = None
    allowed_transitions: list[BookingStatus]
    timeline: list[TimelineEvent]

    created_at: datetime
    updated_at: datetime


class StatusChangeRequest(CamelModel):
    """Move a booking to another status."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )

    status: BookingStatus
    #: Optional reason, kept on the audit row. Especially wanted for a
    #: cancellation, where "why" is the whole question later.
    note: str | None = Field(default=None, max_length=MAX_STATUS_NOTE)


class AssignDriverRequest(CamelModel):
    """Attach a driver to a booking."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )

    driver_id: str


class InternalNotesRequest(CamelModel):
    """Replace the staff-only notes."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )

    internal_notes: str | None = Field(default=None, max_length=MAX_INTERNAL_NOTES)


class BookingStatusOption(CamelModel):
    """One status, as published by `GET /admin/booking-statuses`.

    The frontend generates its own copy from this rather than restating the
    transition table — see `scripts/sync-booking-status.mjs` in the frontend
    and the gate test that fails when the generated file goes stale.
    """

    id: BookingStatus
    label: str
    allowed_transitions: list[BookingStatus]
    is_terminal: bool


class MetricsResponse(CamelModel):
    """The overview screen's numbers."""

    by_status: dict[str, int]
    total: int
    today: int
    unassigned: int
    open_bookings: int
    #: None until at least one booking has been assigned, so the dashboard can
    #: say "no data yet" instead of showing a confident zero.
    median_hours_to_assign: float | None = None
    recent: list[BookingListItem]


class AdminError(CamelModel):
    """Failure body, matching the envelope used everywhere else."""

    ok: Literal[False] = False
    code: Literal[
        "not_found",
        "illegal_transition",
        "driver_not_assignable",
        "validation_error",
        "unavailable",
    ]
    message: str
