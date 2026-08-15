"""The booking lifecycle: statuses, labels and legal transitions.

This is the only definition of the transition table that exists anywhere in the
system. Four things read it and none of them restate it:

* `app.models.booking` builds its CHECK constraint from `BookingStatus`
* `app.services.booking_status` enforces `can_transition` on every write
* `GET /api/v1/admin/booking-statuses` publishes it
* the frontend *generates* `src/lib/constants/booking-status.generated.ts`
  from that endpoint, and a gate test there fails if the file goes stale

Deliberately free of SQLAlchemy, Pydantic and FastAPI imports so every layer
can depend on it without a cycle.
"""

from enum import StrEnum
from typing import Final


class BookingStatus(StrEnum):
    """Where a booking is in its lifecycle.

    String-valued so the database stores a readable token rather than an
    ordinal. An ordinal would renumber the moment a status is inserted in the
    middle, silently rewriting the meaning of every existing row.
    """

    NEW = "new"
    MATCHED = "matched"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


#: Human-readable label per status. Lives with the statuses so the console and
#: the API can never disagree about what `in_progress` is called.
STATUS_LABELS: Final[dict[BookingStatus, str]] = {
    BookingStatus.NEW: "New request",
    BookingStatus.MATCHED: "Driver matched",
    BookingStatus.CONFIRMED: "Confirmed",
    BookingStatus.IN_PROGRESS: "Trip in progress",
    BookingStatus.COMPLETED: "Completed",
    BookingStatus.CANCELLED: "Cancelled",
}

#: What each status may become.
#:
#: `matched -> new` exists because un-assigning a driver has to put the booking
#: back in the queue; without it a mis-assignment could only be cancelled.
#: `completed` and `cancelled` are terminal: a finished trip that can be
#: reopened is an audit problem, and reversing one is a new booking.
TRANSITIONS: Final[dict[BookingStatus, frozenset[BookingStatus]]] = {
    BookingStatus.NEW: frozenset({BookingStatus.MATCHED, BookingStatus.CANCELLED}),
    BookingStatus.MATCHED: frozenset(
        {BookingStatus.CONFIRMED, BookingStatus.NEW, BookingStatus.CANCELLED}
    ),
    BookingStatus.CONFIRMED: frozenset(
        {BookingStatus.IN_PROGRESS, BookingStatus.CANCELLED}
    ),
    BookingStatus.IN_PROGRESS: frozenset(
        {BookingStatus.COMPLETED, BookingStatus.CANCELLED}
    ),
    BookingStatus.COMPLETED: frozenset(),
    BookingStatus.CANCELLED: frozenset(),
}

#: The status every new booking starts in.
INITIAL_STATUS: Final[BookingStatus] = BookingStatus.NEW

#: Statuses that count as an open booking for the backlog metric.
OPEN_STATUSES: Final[frozenset[BookingStatus]] = frozenset(
    {
        BookingStatus.NEW,
        BookingStatus.MATCHED,
        BookingStatus.CONFIRMED,
        BookingStatus.IN_PROGRESS,
    }
)


def is_terminal(status: BookingStatus) -> bool:
    """True when no transition leads out of `status`."""
    return not TRANSITIONS[status]


def can_transition(current: BookingStatus, target: BookingStatus) -> bool:
    """True when `current -> target` is a legal move.

    A no-op move (`current == target`) is not legal. Allowing it would write an
    audit event recording a change that did not happen.
    """
    return target in TRANSITIONS[current]


def allowed_transitions(current: BookingStatus) -> tuple[BookingStatus, ...]:
    """Legal targets from `current`, ordered the way a menu should offer them.

    Forward moves first, then backward ones, then `cancelled` last — so
    `matched` offers "Confirmed, New, Cancelled" rather than the enum's own
    order, which would put the un-assign above the move everyone actually
    wants. Alphabetical would be worse still: it leads with "Cancelled".

    Ordering is decided here, once, rather than in the console, so the API
    response and any other client agree on it.
    """
    order = list(BookingStatus)
    position = order.index(current)

    def rank(target: BookingStatus) -> tuple[int, int]:
        index = order.index(target)
        if target is BookingStatus.CANCELLED:
            return (2, index)
        return (0 if index > position else 1, index)

    return tuple(sorted(TRANSITIONS[current], key=rank))
