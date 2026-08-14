"""Integration lane: hits the real Neon database.

Excluded from the gate (`-m "not integration"`) because it is neither offline
nor free. Run it before shipping a schema change:

    pytest -m integration

Skips rather than fails when DATABASE_URL is absent, so a fresh clone without
credentials still reports a clean run.

Every row written here is deleted in the same test. Nothing is left behind, and
no `nibbs_` table is read or written.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.db.session import dispose_engines, get_sessionmaker
from app.models.booking import Booking
from app.schemas.booking import BookingRequest
from app.services.booking import create_booking

pytestmark = pytest.mark.integration

OTHER_APP_TABLES = {
    "nibbs_banks",
    "nibbs_nip_bank_codes",
    "nibbs_password_resets",
    "nibbs_schedule_reports",
    "nibbs_sessions",
    "nibbs_users",
}


@pytest.fixture(autouse=True)
async def _dispose_between_tests() -> AsyncIterator[None]:
    """Drop pooled connections after every test.

    `app.db.session` memoizes an engine per URL, and its pooled asyncpg
    connections are bound to the event loop that opened them. pytest-asyncio
    gives each test a fresh loop, so a reused connection fails with
    "got Future attached to a different loop". Production has one loop for the
    life of the process, so the cache is correct there; only the test harness
    needs this. Teardown runs inside the test's own loop, which is what makes
    the disposal legal.
    """
    yield
    await dispose_engines()


@pytest.fixture
def neon_settings() -> Settings:
    settings = get_settings()
    if not settings.is_database_configured:
        pytest.skip("DATABASE_URL is not set")
    return settings


def _request(**overrides: Any) -> BookingRequest:
    body: dict[str, Any] = {
        "fullName": "Integration Probe",
        "phone": "+234 800 000 0000",
        "email": "integration@example.com",
        "tripType": "corporate",
        "pickupLocation": "Test Origin",
        "destination": "Test Destination",
        "startDate": (datetime.now(UTC) + timedelta(days=5)).date().isoformat(),
        "durationDays": 3,
        "passengers": 2,
        "notes": "Written and deleted by the integration lane.",
    }
    body.update(overrides)
    return BookingRequest.model_validate(body)


async def test_connects_to_neon(neon_settings: Settings) -> None:
    assert neon_settings.database_url is not None
    async with get_sessionmaker(neon_settings.database_url)() as session:
        assert (await session.execute(text("select 1"))).scalar_one() == 1


async def test_migration_is_applied(neon_settings: Settings) -> None:
    """`ease_bookings` must exist, or the deploy forgot `alembic upgrade head`."""
    assert neon_settings.database_url is not None
    async with get_sessionmaker(neon_settings.database_url)() as session:
        found = (
            await session.execute(
                text(
                    "select 1 from information_schema.tables "
                    "where table_schema = 'public' and table_name = 'ease_bookings'"
                )
            )
        ).scalar_one_or_none()
    assert found == 1


async def test_round_trips_a_booking(neon_settings: Settings) -> None:
    """The full path against real Postgres: types, constraints, defaults."""
    assert neon_settings.database_url is not None
    factory = get_sessionmaker(neon_settings.database_url)

    async with factory() as session:
        booking = await create_booking(session, _request())
        booking_id = booking.id
        reference = booking.reference

    try:
        async with factory() as session:
            stored = (
                await session.execute(select(Booking).where(Booking.id == booking_id))
            ).scalar_one()
            assert stored.reference == reference
            assert stored.email == "integration@example.com"
            assert stored.duration_days == 3
            # Postgres returns an aware datetime for timestamptz; SQLite does
            # not, so this assertion only means anything here.
            assert stored.created_at.tzinfo is not None
    finally:
        async with factory() as session:
            await session.execute(delete(Booking).where(Booking.id == booking_id))
            await session.commit()

    async with factory() as session:
        gone = (
            await session.execute(select(Booking).where(Booking.id == booking_id))
        ).scalar_one_or_none()
    assert gone is None


async def test_check_constraints_are_enforced_by_postgres(
    neon_settings: Settings,
) -> None:
    """The API rejects this first; the database must refuse it too."""
    assert neon_settings.database_url is not None
    factory = get_sessionmaker(neon_settings.database_url)

    async with factory() as session:
        session.add(
            Booking(
                reference="ED-BADROW",
                full_name="Constraint Probe",
                phone="+2348000000000",
                email="constraint@example.com",
                trip_type="airport",
                pickup_location="A",
                destination="B",
                start_date=datetime.now(UTC).date(),
                duration_days=999,  # violates ease_bookings_duration_days_range
                passengers=1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_leaves_the_other_apps_tables_alone(neon_settings: Settings) -> None:
    """This database is shared with nibbs-report. Its tables must still exist."""
    assert neon_settings.database_url is not None
    async with get_sessionmaker(neon_settings.database_url)() as session:
        rows = await session.execute(
            text(
                "select table_name from information_schema.tables "
                "where table_schema = 'public'"
            )
        )
        present = {row[0] for row in rows}

    assert present >= OTHER_APP_TABLES, f"missing: {OTHER_APP_TABLES - present}"
