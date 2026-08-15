"""The driver registry. Knows nothing about HTTP.

There is no delete. A driver who fails vetting or stops working is deactivated,
never removed, because bookings point at them and the record of who drove a
trip is the thing you most need after something goes wrong. `is_active=False`
takes them out of the assignment picker; the history stays.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vetting import VettingStatus, is_assignable
from app.models.driver import Driver

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class DriverNotFoundError(Exception):
    """No driver with that id."""


@dataclass(frozen=True, slots=True)
class DriverFilters:
    """How the console narrows the driver list."""

    vetting_statuses: tuple[VettingStatus, ...] = ()
    #: None means "either"; the picker passes True.
    active: bool | None = None
    #: Only drivers who can actually take a booking right now.
    assignable_only: bool = False
    query: str | None = None


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_drivers(
    session: AsyncSession,
    *,
    filters: DriverFilters | None = None,
    page: int = 1,
    limit: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[Driver], int]:
    """Return a page of drivers, alphabetical, with the unpaged total."""
    filters = filters or DriverFilters()
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    page = max(1, page)

    statement = select(Driver)
    counter = select(func.count()).select_from(Driver)

    if filters.assignable_only:
        # The query form of `app.domain.vetting.is_assignable`. The two are
        # pinned together by a test, because a picker that offers a driver the
        # assign endpoint then rejects is worse than one that offers nobody.
        conditions = (
            Driver.vetting_status == VettingStatus.VERIFIED.value,
            Driver.is_active.is_(True),
        )
        statement = statement.where(*conditions)
        counter = counter.where(*conditions)
    else:
        if filters.vetting_statuses:
            values = [status.value for status in filters.vetting_statuses]
            statement = statement.where(Driver.vetting_status.in_(values))
            counter = counter.where(Driver.vetting_status.in_(values))

        if filters.active is not None:
            statement = statement.where(Driver.is_active.is_(filters.active))
            counter = counter.where(Driver.is_active.is_(filters.active))

    if filters.query and filters.query.strip():
        pattern = f"%{_escape_like(filters.query.strip())}%"
        matches = or_(
            Driver.full_name.ilike(pattern, escape="\\"),
            Driver.phone.ilike(pattern, escape="\\"),
            Driver.vehicle_plate.ilike(pattern, escape="\\"),
        )
        statement = statement.where(matches)
        counter = counter.where(matches)

    total = await session.scalar(counter)
    rows = await session.scalars(
        statement.order_by(Driver.full_name, Driver.id)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list(rows.all()), int(total or 0)


async def get_driver(session: AsyncSession, driver_id: uuid.UUID) -> Driver:
    """Load a driver or raise."""
    driver = await session.get(Driver, driver_id)
    if driver is None:
        raise DriverNotFoundError(str(driver_id))
    return driver


async def create_driver(session: AsyncSession, **fields: object) -> Driver:
    """Add a driver. New drivers start `pending` unless told otherwise."""
    driver = Driver(**fields)
    session.add(driver)
    await session.commit()
    await session.refresh(driver)
    return driver


async def update_driver(
    session: AsyncSession, driver: Driver, **fields: object
) -> Driver:
    """Apply a partial update.

    Only keys actually present are written, so a PATCH that omits a field
    leaves it alone instead of nulling it.
    """
    for name, value in fields.items():
        setattr(driver, name, value)

    await session.commit()
    await session.refresh(driver)
    return driver


def can_take_bookings(driver: Driver) -> bool:
    """Whether this driver may be assigned, per the domain rule."""
    return is_assignable(VettingStatus(driver.vetting_status), driver.is_active)
