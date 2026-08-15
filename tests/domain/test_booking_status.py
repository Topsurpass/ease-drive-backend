"""Gate tests for the booking lifecycle table.

This table is the single definition of the lifecycle: the CHECK constraint, the
service, the API and the frontend's generated copy all derive from it. So the
tests here are exhaustive rather than representative — every pair of statuses is
checked, in both directions, not just the ones someone thought to list.
"""

import itertools

import pytest

from app.domain.booking_status import (
    INITIAL_STATUS,
    OPEN_STATUSES,
    STATUS_LABELS,
    TRANSITIONS,
    BookingStatus,
    allowed_transitions,
    can_transition,
    is_terminal,
)

#: Spelled out independently of `TRANSITIONS`, so this is a real second opinion
#: rather than a restatement of the thing under test. If someone edits the
#: lifecycle, both have to change and the disagreement is caught here.
LEGAL: set[tuple[BookingStatus, BookingStatus]] = {
    (BookingStatus.NEW, BookingStatus.MATCHED),
    (BookingStatus.NEW, BookingStatus.CANCELLED),
    (BookingStatus.MATCHED, BookingStatus.CONFIRMED),
    (BookingStatus.MATCHED, BookingStatus.NEW),
    (BookingStatus.MATCHED, BookingStatus.CANCELLED),
    (BookingStatus.CONFIRMED, BookingStatus.IN_PROGRESS),
    (BookingStatus.CONFIRMED, BookingStatus.CANCELLED),
    (BookingStatus.IN_PROGRESS, BookingStatus.COMPLETED),
    (BookingStatus.IN_PROGRESS, BookingStatus.CANCELLED),
}


@pytest.mark.parametrize(
    ("current", "target"), list(itertools.product(BookingStatus, BookingStatus))
)
def test_every_pair_of_statuses(current: BookingStatus, target: BookingStatus) -> None:
    """All 36 combinations, so no illegal move is legal by omission."""
    assert can_transition(current, target) is ((current, target) in LEGAL)


@pytest.mark.parametrize("status", list(BookingStatus))
def test_a_status_can_never_transition_to_itself(status: BookingStatus) -> None:
    """A no-op move would write an audit event for a change that did not happen."""
    assert not can_transition(status, status)


def test_completed_is_terminal() -> None:
    """Reopening a finished trip is an audit problem; it is a new booking."""
    assert is_terminal(BookingStatus.COMPLETED)


def test_cancelled_is_terminal() -> None:
    assert is_terminal(BookingStatus.CANCELLED)


@pytest.mark.parametrize(
    "status",
    [
        BookingStatus.NEW,
        BookingStatus.MATCHED,
        BookingStatus.CONFIRMED,
        BookingStatus.IN_PROGRESS,
    ],
)
def test_open_statuses_are_not_terminal(status: BookingStatus) -> None:
    assert not is_terminal(status)


def test_every_status_can_be_cancelled_until_it_is_finished() -> None:
    """A trip that cannot be called off is a support ticket waiting to happen."""
    for status in BookingStatus:
        if is_terminal(status):
            continue
        assert can_transition(status, BookingStatus.CANCELLED), status


def test_every_status_is_reachable_from_the_initial_one() -> None:
    """A status nothing can reach is dead code in the database."""
    reachable = {INITIAL_STATUS}
    frontier = [INITIAL_STATUS]
    while frontier:
        for target in TRANSITIONS[frontier.pop()]:
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)

    assert reachable == set(BookingStatus)


# --- menu ordering ----------------------------------------------------------


def test_allowed_transitions_puts_the_forward_move_first() -> None:
    """`matched` should offer "Confirmed" before "New", not the enum's order."""
    assert allowed_transitions(BookingStatus.MATCHED) == (
        BookingStatus.CONFIRMED,
        BookingStatus.NEW,
        BookingStatus.CANCELLED,
    )


@pytest.mark.parametrize("status", list(BookingStatus))
def test_cancelled_is_always_offered_last(status: BookingStatus) -> None:
    """The destructive option must never be the first thing in a menu."""
    options = allowed_transitions(status)
    if BookingStatus.CANCELLED in options:
        assert options[-1] is BookingStatus.CANCELLED


@pytest.mark.parametrize("status", list(BookingStatus))
def test_allowed_transitions_agrees_with_can_transition(
    status: BookingStatus,
) -> None:
    """The menu and the enforcement must not be able to disagree."""
    assert set(allowed_transitions(status)) == {
        target for target in BookingStatus if can_transition(status, target)
    }


# --- completeness -----------------------------------------------------------


@pytest.mark.parametrize("status", list(BookingStatus))
def test_every_status_has_a_label(status: BookingStatus) -> None:
    """A status without a label renders as a raw token in the console."""
    assert STATUS_LABELS[status].strip()


@pytest.mark.parametrize("status", list(BookingStatus))
def test_every_status_appears_in_the_transition_table(
    status: BookingStatus,
) -> None:
    """A missing key would be a KeyError at runtime, on a real booking."""
    assert status in TRANSITIONS


def test_open_statuses_are_exactly_the_non_terminal_ones() -> None:
    """The backlog metric counts these; drift would make it quietly wrong."""
    assert (
        frozenset(status for status in BookingStatus if not is_terminal(status))
        == OPEN_STATUSES
    )
