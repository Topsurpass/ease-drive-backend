"""Driver vetting state.

"Vetted drivers" is the promise the landing page makes, so the state that backs
that word is domain logic, not a free-text column. Only a `VERIFIED` driver can
be assigned to a booking; the check lives here so the service and the schema
ask the same question.
"""

from enum import StrEnum


class VettingStatus(StrEnum):
    """How far a driver has got through vetting."""

    #: Recorded, not yet checked. Cannot take bookings.
    PENDING = "pending"
    #: Documents and references checked. Assignable.
    VERIFIED = "verified"
    #: Failed vetting. Kept rather than deleted, so the decision is auditable.
    REJECTED = "rejected"


def is_assignable(status: VettingStatus, is_active: bool) -> bool:
    """True when a driver may be assigned to a booking.

    Both conditions matter and they mean different things: vetting is about
    whether the driver was ever cleared, `is_active` about whether they are
    working right now. A verified driver on leave is not assignable, and an
    active but unvetted one certainly is not.
    """
    return status is VettingStatus.VERIFIED and is_active
