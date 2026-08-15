"""Gate tests for the ops console's booking endpoints.

Three things get the most attention here, because they are the three that hurt
if they are wrong:

* **Authorization.** Every route refuses an anonymous caller.
* **The PII contract.** The list masks contact details and the detail endpoint
  logs the look. A regression here leaks every customer's phone number to
  anyone who can load a table.
* **Illegal transitions.** 409, not 500, and nothing written.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.domain.roles import UserRole
from app.domain.vetting import VettingStatus
from app.main import create_app
from app.models.booking import Booking
from app.models.booking_event import BookingEvent, BookingEventType
from app.models.driver import Driver
from app.services.auth import authenticate, create_user

PASSWORD = "a-perfectly-fine-passphrase"
BOOKINGS = "/api/v1/admin/bookings"
METRICS = "/api/v1/admin/metrics"
STATUSES = "/api/v1/admin/booking-statuses"


@pytest.fixture
def client(settings: Settings, db_session: AsyncSession) -> Iterator[TestClient]:
    """A client bound to the in-memory database, with auth configured."""
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _session_override
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


async def _token(
    db_session: AsyncSession,
    settings: Settings,
    *,
    role: UserRole = UserRole.OPS,
    email: str = "ops@example.com",
) -> str:
    await create_user(
        db_session,
        email=email,
        password=PASSWORD,
        full_name="Ops Person",
        role=role.value,
    )
    tokens = await authenticate(
        db_session, email=email, password=PASSWORD, settings=settings
    )
    return tokens.access_token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _booking(session: AsyncSession, **overrides: Any) -> Booking:
    fields: dict[str, Any] = {
        "reference": "ED-AAA111",
        "full_name": "Ada Lovelace",
        "phone": "+234 800 123 4512",
        "email": "ada.lovelace@example.com",
        "trip_type": "airport",
        "pickup_location": "Ikeja GRA",
        "destination": "Murtala Muhammed Airport",
        "start_date": (datetime.now(UTC) + timedelta(days=3)).date(),
        "duration_days": 2,
        "passengers": 3,
        "notes": "Two large suitcases.",
    }
    fields.update(overrides)
    record = Booking(**fields)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def _driver(session: AsyncSession, **overrides: Any) -> Driver:
    fields: dict[str, Any] = {
        "full_name": "Grace Hopper",
        "phone": "+234 801 111 1111",
        "vetting_status": VettingStatus.VERIFIED.value,
        "is_active": True,
    }
    fields.update(overrides)
    record = Driver(**fields)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


# --- authorization ----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", BOOKINGS),
        ("get", f"{BOOKINGS}/ED-AAA111"),
        ("get", METRICS),
        ("get", STATUSES),
        ("patch", f"{BOOKINGS}/ED-AAA111/status"),
        ("post", f"{BOOKINGS}/ED-AAA111/assign"),
        ("post", f"{BOOKINGS}/ED-AAA111/unassign"),
        ("patch", f"{BOOKINGS}/ED-AAA111/notes"),
    ],
)
def test_every_route_refuses_an_anonymous_caller(
    client: TestClient, method: str, path: str
) -> None:
    """The whole console is behind auth, with no exceptions to remember."""
    call = getattr(client, method)
    response = call(path) if method == "get" else call(path, json={})
    assert response.status_code == 401, path


def test_a_garbage_token_is_refused(client: TestClient) -> None:
    response = client.get(BOOKINGS, headers=_auth("not-a-real-token"))
    assert response.status_code == 401


# --- the PII contract -------------------------------------------------------


async def test_the_list_masks_contact_details(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """One request returns 25 customers. Almost none of them need to be readable."""
    token = await _token(db_session, settings)
    await _booking(db_session)

    body = client.get(BOOKINGS, headers=_auth(token)).json()
    row = body["items"][0]

    assert row["phoneMasked"] == "+••• ••• ••• ••12"
    assert row["emailMasked"] == "a•••••••••••@example.com"
    assert row["customerName"] == "Ada L."


async def test_the_list_never_carries_a_real_phone_or_email(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Asserted on the raw response text, so a stray field cannot slip through."""
    token = await _token(db_session, settings)
    await _booking(db_session)

    text = client.get(BOOKINGS, headers=_auth(token)).text

    assert "+234 800 123 4512" not in text
    assert "ada.lovelace@example.com" not in text
    assert "Lovelace" not in text
    # The customer's own note is not in the list at all.
    assert "Two large suitcases." not in text


async def test_the_list_says_whether_a_note_exists_without_showing_it(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session)

    row = client.get(BOOKINGS, headers=_auth(token)).json()["items"][0]
    assert row["hasNotes"] is True


async def test_the_detail_endpoint_returns_the_real_details(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session)

    body = client.get(f"{BOOKINGS}/ED-AAA111", headers=_auth(token)).json()

    assert body["phone"] == "+234 800 123 4512"
    assert body["email"] == "ada.lovelace@example.com"
    assert body["fullName"] == "Ada Lovelace"
    assert body["notes"] == "Two large suitcases."


async def test_opening_a_booking_writes_an_audit_row(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Looking at a customer's real number must never be untraceable."""
    token = await _token(db_session, settings)
    booking = await _booking(db_session)

    client.get(f"{BOOKINGS}/ED-AAA111", headers=_auth(token))

    events = (
        await db_session.scalars(
            select(BookingEvent).where(
                BookingEvent.booking_id == booking.id,
                BookingEvent.event_type == BookingEventType.VIEWED.value,
            )
        )
    ).all()
    assert len(events) == 1
    assert events[0].actor_user_id is not None


# --- listing ----------------------------------------------------------------


async def test_an_empty_list_is_a_200_with_an_empty_page(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """The console renders an empty state; it should not have to catch a 404."""
    token = await _token(db_session, settings)

    body = client.get(BOOKINGS, headers=_auth(token)).json()

    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 1


async def test_bookings_come_back_newest_first(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    older = await _booking(db_session, reference="ED-OLD001")
    older.created_at = datetime.now(UTC) - timedelta(days=2)
    await db_session.commit()
    await _booking(db_session, reference="ED-NEW001")

    items = client.get(BOOKINGS, headers=_auth(token)).json()["items"]
    assert [row["reference"] for row in items] == ["ED-NEW001", "ED-OLD001"]


async def test_pagination_splits_the_results(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    for index in range(5):
        await _booking(db_session, reference=f"ED-P{index:05d}")

    first = client.get(f"{BOOKINGS}?limit=2&page=1", headers=_auth(token)).json()
    second = client.get(f"{BOOKINGS}?limit=2&page=2", headers=_auth(token)).json()

    assert first["total"] == second["total"] == 5
    assert first["pages"] == 3
    assert len(first["items"]) == len(second["items"]) == 2
    # No row appears on two pages.
    assert not {row["reference"] for row in first["items"]} & {
        row["reference"] for row in second["items"]
    }


async def test_filtering_by_status(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session, reference="ED-NEW002")
    await _booking(db_session, reference="ED-CAN001", status="cancelled")

    body = client.get(f"{BOOKINGS}?status=cancelled", headers=_auth(token)).json()

    assert [row["reference"] for row in body["items"]] == ["ED-CAN001"]


async def test_filtering_by_assignment(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """The unassigned backlog is the number the console exists to shrink."""
    token = await _token(db_session, settings)
    driver = await _driver(db_session)
    await _booking(db_session, reference="ED-FREE01")
    await _booking(db_session, reference="ED-BUSY01", assigned_driver_id=driver.id)

    unassigned = client.get(f"{BOOKINGS}?assigned=false", headers=_auth(token)).json()
    assert [row["reference"] for row in unassigned["items"]] == ["ED-FREE01"]


@pytest.mark.parametrize(
    "term", ["ED-AAA111", "aaa111", "Ada", "lovelace@example", "4512"]
)
async def test_search_matches_reference_name_email_and_phone(
    client: TestClient, db_session: AsyncSession, settings: Settings, term: str
) -> None:
    """Staff search with whatever the customer just read out over the phone."""
    token = await _token(db_session, settings)
    await _booking(db_session)
    # Every searchable field differs, or the "no match" half proves nothing.
    await _booking(
        db_session,
        reference="ED-ZZZ999",
        full_name="Someone Else",
        phone="+234 909 777 8888",
        email="someone@elsewhere.test",
    )

    body = client.get(f"{BOOKINGS}?q={term}", headers=_auth(token)).json()
    assert [row["reference"] for row in body["items"]] == ["ED-AAA111"]


async def test_a_search_wildcard_is_treated_as_a_literal(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """A bare % must not return the whole table."""
    token = await _token(db_session, settings)
    await _booking(db_session)

    body = client.get(f"{BOOKINGS}?q=%", headers=_auth(token)).json()
    assert body["items"] == []


async def test_an_oversized_limit_is_rejected(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """There is no unbounded read of this table, by construction."""
    token = await _token(db_session, settings)
    assert (
        client.get(f"{BOOKINGS}?limit=100000", headers=_auth(token)).status_code == 422
    )


# --- transitions ------------------------------------------------------------


async def test_a_legal_status_change_succeeds(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session)

    response = client.patch(
        f"{BOOKINGS}/ED-AAA111/status",
        json={"status": "cancelled"},
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_an_illegal_status_change_is_409_and_readable(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """409, not 422: the request is fine, the booking's state conflicts."""
    token = await _token(db_session, settings)
    await _booking(db_session)

    response = client.patch(
        f"{BOOKINGS}/ED-AAA111/status",
        json={"status": "completed"},
        headers=_auth(token),
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "illegal_transition"
    # Names both ends using the human labels, so the console can show the
    # message as-is rather than translating a status token.
    assert "New request" in body["message"]
    assert "Completed" in body["message"]


async def test_the_detail_response_lists_only_legal_next_steps(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """The console builds its menu from this, so it cannot offer an illegal move."""
    token = await _token(db_session, settings)
    await _booking(db_session)

    body = client.get(f"{BOOKINGS}/ED-AAA111", headers=_auth(token)).json()
    assert body["allowedTransitions"] == ["matched", "cancelled"]


async def test_an_unknown_reference_is_404(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    response = client.get(f"{BOOKINGS}/ED-NOPE00", headers=_auth(token))

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# --- assignment -------------------------------------------------------------


async def test_assigning_a_driver_matches_the_booking(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session)
    driver = await _driver(db_session)

    response = client.post(
        f"{BOOKINGS}/ED-AAA111/assign",
        json={"driverId": str(driver.id)},
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "matched"
    assert body["driver"]["fullName"] == "Grace Hopper"


async def test_assigning_an_unvetted_driver_is_422(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session)
    driver = await _driver(db_session, vetting_status=VettingStatus.PENDING.value)

    response = client.post(
        f"{BOOKINGS}/ED-AAA111/assign",
        json={"driverId": str(driver.id)},
        headers=_auth(token),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "driver_not_assignable"


async def test_assigning_a_malformed_driver_id_is_422(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session)

    response = client.post(
        f"{BOOKINGS}/ED-AAA111/assign",
        json={"driverId": "not-a-uuid"},
        headers=_auth(token),
    )
    assert response.status_code == 422


async def test_unassigning_returns_the_booking_to_the_queue(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session)
    driver = await _driver(db_session)
    client.post(
        f"{BOOKINGS}/ED-AAA111/assign",
        json={"driverId": str(driver.id)},
        headers=_auth(token),
    )

    body = client.post(f"{BOOKINGS}/ED-AAA111/unassign", headers=_auth(token)).json()

    assert body["driver"] is None
    assert body["status"] == "new"


# --- notes ------------------------------------------------------------------


async def test_internal_notes_round_trip(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session)

    body = client.patch(
        f"{BOOKINGS}/ED-AAA111/notes",
        json={"internalNotes": "Called; happy to wait."},
        headers=_auth(token),
    ).json()

    assert body["internalNotes"] == "Called; happy to wait."


async def test_internal_notes_never_appear_in_the_list(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Staff-only means staff-only, including from the widest response."""
    token = await _token(db_session, settings)
    await _booking(db_session)
    client.patch(
        f"{BOOKINGS}/ED-AAA111/notes",
        json={"internalNotes": "Do not tell the customer this."},
        headers=_auth(token),
    )

    assert (
        "Do not tell the customer this."
        not in client.get(BOOKINGS, headers=_auth(token)).text
    )


# --- the transition table, as published -------------------------------------


async def test_booking_statuses_publishes_the_whole_lifecycle(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """The frontend generates its copy from this response."""
    token = await _token(db_session, settings)

    body = client.get(STATUSES, headers=_auth(token)).json()

    assert [row["id"] for row in body] == [
        "new",
        "matched",
        "confirmed",
        "in_progress",
        "completed",
        "cancelled",
    ]
    by_id = {row["id"]: row for row in body}
    assert by_id["matched"]["allowedTransitions"] == [
        "confirmed",
        "new",
        "cancelled",
    ]
    assert by_id["completed"]["isTerminal"] is True
    assert by_id["new"]["label"] == "New request"


# --- metrics ----------------------------------------------------------------


async def test_metrics_counts_by_status_and_backlog(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    token = await _token(db_session, settings)
    await _booking(db_session, reference="ED-MET001")
    await _booking(db_session, reference="ED-MET002", status="cancelled")

    body = client.get(METRICS, headers=_auth(token)).json()

    assert body["total"] == 2
    assert body["byStatus"]["new"] == 1
    # A cancelled booking is not backlog.
    assert body["unassigned"] == 1
    assert body["openBookings"] == 1


async def test_median_time_to_assign_is_null_until_something_is_assigned(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Null, not zero: zero would read as "instant" on the dashboard."""
    token = await _token(db_session, settings)
    await _booking(db_session)

    assert (
        client.get(METRICS, headers=_auth(token)).json()["medianHoursToAssign"] is None
    )


async def test_median_time_to_assign_is_reported_once_a_driver_is_attached(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """The metric this whole phase exists to make measurable."""
    token = await _token(db_session, settings)
    await _booking(db_session)
    driver = await _driver(db_session)
    client.post(
        f"{BOOKINGS}/ED-AAA111/assign",
        json={"driverId": str(driver.id)},
        headers=_auth(token),
    )

    median = client.get(METRICS, headers=_auth(token)).json()["medianHoursToAssign"]
    assert median is not None
    assert median >= 0


async def test_metrics_recent_list_is_also_masked(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """The overview shows recent bookings; that is a list view too."""
    token = await _token(db_session, settings)
    await _booking(db_session)

    assert "+234 800 123 4512" not in client.get(METRICS, headers=_auth(token)).text
