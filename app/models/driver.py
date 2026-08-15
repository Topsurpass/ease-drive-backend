"""Driver registry.

A driver is assignable only when vetting has cleared them *and* they are
currently active — see `app.domain.vetting.is_assignable`. Rejected drivers are
kept rather than deleted so the decision stays auditable, which is also why
there is no delete path in the API, only `is_active = false`.

Column limits mirror `ease_bookings` where the field is the same kind of thing
(name, phone, email), so a driver's contact details cannot be longer than a
customer's.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.vetting import VettingStatus

MAX_NAME = 80
MAX_PHONE = 20
MAX_EMAIL = 254
MAX_VETTING_STATUS = 16
MAX_VEHICLE_FIELD = 60
MAX_PLATE = 16

_VETTING_VALUES = ", ".join(f"'{status.value}'" for status in VettingStatus)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Driver(Base):
    """A driver the business can assign to a booking."""

    __tablename__ = "ease_drivers"
    __table_args__ = (
        # Deliberate duplication of the enum, same reasoning as the booking
        # table's range checks: the API rejects a bad value first, but a script
        # or a psql session writing directly cannot store a status the domain
        # does not recognise.
        CheckConstraint(
            f"vetting_status IN ({_VETTING_VALUES})",
            name="ease_drivers_vetting_status_valid",
        ),
        Index("ease_drivers_vetting_status_idx", "vetting_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    full_name: Mapped[str] = mapped_column(String(MAX_NAME))
    phone: Mapped[str] = mapped_column(String(MAX_PHONE))
    email: Mapped[str | None] = mapped_column(String(MAX_EMAIL), nullable=True)

    vetting_status: Mapped[str] = mapped_column(
        String(MAX_VETTING_STATUS), default=VettingStatus.PENDING.value
    )

    vehicle_make: Mapped[str | None] = mapped_column(
        String(MAX_VEHICLE_FIELD), nullable=True
    )
    vehicle_model: Mapped[str | None] = mapped_column(
        String(MAX_VEHICLE_FIELD), nullable=True
    )
    vehicle_plate: Mapped[str | None] = mapped_column(String(MAX_PLATE), nullable=True)

    # Not unique: a plate can legitimately move between drivers when a vehicle
    # is reassigned, and a unique index would block recording that.
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<Driver {self.full_name} {self.vetting_status}>"
