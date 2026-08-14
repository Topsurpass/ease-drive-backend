"""Booking wire contract.

Mirrors `ease-drive-frontend/src/lib/validators/booking.schema.ts` and
`src/services/booking/index.ts`. The frontend calls that zod schema the single
source of truth and says the real backend should validate the same shape, so
every limit here is copied from it deliberately, not invented.

Field names are camelCase on the wire because that is what the zod schema
emits. `populate_by_name` also accepts snake_case, so a Python client does not
have to speak JavaScript.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic.alias_generators import to_camel

# Copied from siteConfig.booking.tripTypes in the frontend.
TripType = Literal[
    "interstate",
    "intrastate",
    "private-driver",
    "family-group",
    "airport",
    "corporate",
    "events",
]

# Copied from siteConfig.booking.limits.
MIN_DAYS: Final[int] = 1
MAX_DAYS: Final[int] = 30
MIN_PASSENGERS: Final[int] = 1
MAX_PASSENGERS: Final[int] = 14
MAX_NOTES: Final[int] = 500

# The frontend's PHONE_PATTERN, unchanged.
PHONE_PATTERN: Final[str] = r"^\+?[\d\s()\-]{7,20}$"
MIN_PHONE_DIGITS: Final[int] = 7


class BookingRequest(BaseModel):
    """The body posted by the booking form."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    full_name: str = Field(min_length=2, max_length=80)
    phone: str = Field(pattern=PHONE_PATTERN)
    email: EmailStr
    trip_type: TripType
    pickup_location: str = Field(min_length=2, max_length=120)
    destination: str = Field(min_length=2, max_length=120)
    start_date: date
    duration_days: int = Field(ge=MIN_DAYS, le=MAX_DAYS)
    passengers: int = Field(ge=MIN_PASSENGERS, le=MAX_PASSENGERS)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)

    @field_validator("phone")
    @classmethod
    def _has_enough_digits(cls, value: str) -> str:
        """The pattern allows spaces and brackets; this counts actual digits."""
        digits = sum(character.isdigit() for character in value)
        if digits < MIN_PHONE_DIGITS:
            raise ValueError("Phone number looks too short.")
        return value

    @field_validator("start_date")
    @classmethod
    def _not_in_the_past(cls, value: date) -> date:
        """Reject past dates, with a day of slack for time zones.

        The browser compares against *local* midnight. A client in UTC+1 booking
        at 00:30 local is still on the previous UTC day, so comparing against
        UTC today alone would reject a date the form just accepted. One day of
        tolerance covers every real offset without letting last week through.
        """
        earliest = (datetime.now(UTC) - timedelta(days=1)).date()
        if value < earliest:
            raise ValueError("Start date cannot be in the past.")
        return value

    @field_validator("notes")
    @classmethod
    def _blank_notes_are_absent(cls, value: str | None) -> str | None:
        """The form sends "" for an untouched optional field; store NULL."""
        return value or None


class BookingAccepted(BaseModel):
    """Success body. Matches `BookingSuccess` in the frontend's booking service."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    ok: Literal[True] = True
    reference: str
    received_at: datetime


class BookingError(BaseModel):
    """Failure body. Matches `BookingFailure` in the frontend's booking service."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    ok: Literal[False] = False
    code: Literal["transport_error", "validation_error", "unavailable"]
    message: str
