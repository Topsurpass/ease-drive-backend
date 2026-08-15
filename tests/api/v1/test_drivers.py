"""Gate tests for the driver registry.

The role boundary is the point of this file. Reading drivers is open to any
signed-in staff member, because you cannot assign a booking without a list to
pick from. Writing is admin-only, because "verified" is the claim the whole
vetted-drivers promise rests on, and it should not be changeable by an account
that only works the queue.
"""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.domain.roles import UserRole
from app.domain.vetting import VettingStatus
from app.main import create_app
from app.models.driver import Driver
from app.services.auth import authenticate, create_user

PASSWORD = "a-perfectly-fine-passphrase"
DRIVERS = "/api/v1/admin/drivers"


@pytest.fixture
def client(settings: Settings, db_session: AsyncSession) -> Iterator[TestClient]:
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _session_override
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


async def _token(
    db_session: AsyncSession, settings: Settings, role: UserRole
) -> dict[str, str]:
    email = f"{role.value}@example.com"
    await create_user(
        db_session,
        email=email,
        password=PASSWORD,
        full_name=f"{role.value.title()} Person",
        role=role.value,
    )
    tokens = await authenticate(
        db_session, email=email, password=PASSWORD, settings=settings
    )
    return {"Authorization": f"Bearer {tokens.access_token}"}


async def _driver(session: AsyncSession, **overrides: Any) -> Driver:
    fields: dict[str, Any] = {
        "full_name": "Grace Hopper",
        "phone": "+234 801 111 1111",
        "vetting_status": VettingStatus.VERIFIED.value,
        "is_active": True,
        "vehicle_plate": "LAG-123XY",
    }
    fields.update(overrides)
    record = Driver(**fields)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


VALID_DRIVER = {
    "fullName": "Katherine Johnson",
    "phone": "+234 802 222 2222",
    "email": "katherine@example.com",
    "vehicleMake": "Toyota",
    "vehicleModel": "Corolla",
    "vehiclePlate": "ABC-987ZZ",
}


# --- authorization ----------------------------------------------------------


def test_listing_drivers_needs_a_token(client: TestClient) -> None:
    assert client.get(DRIVERS).status_code == 401


async def test_ops_can_read_the_driver_list(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Assignment needs a picker, so reading is not admin-gated."""
    auth = await _token(db_session, settings, UserRole.OPS)
    await _driver(db_session)

    response = client.get(DRIVERS, headers=auth)

    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_ops_cannot_create_a_driver(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """403, not 401: signing in again would not help, and the console says so."""
    auth = await _token(db_session, settings, UserRole.OPS)

    response = client.post(DRIVERS, json=VALID_DRIVER, headers=auth)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_ops_cannot_edit_a_driver(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Specifically: an ops account cannot mark someone verified."""
    auth = await _token(db_session, settings, UserRole.OPS)
    driver = await _driver(db_session, vetting_status=VettingStatus.PENDING.value)

    response = client.patch(
        f"{DRIVERS}/{driver.id}", json={"vettingStatus": "verified"}, headers=auth
    )

    assert response.status_code == 403
    await db_session.refresh(driver)
    assert driver.vetting_status == VettingStatus.PENDING.value


async def test_an_admin_can_create_a_driver(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    auth = await _token(db_session, settings, UserRole.ADMIN)

    response = client.post(DRIVERS, json=VALID_DRIVER, headers=auth)

    assert response.status_code == 201
    assert response.json()["fullName"] == "Katherine Johnson"


# --- creation rules ---------------------------------------------------------


async def test_a_new_driver_starts_unvetted(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Verified has to be a deliberate act, not a default."""
    auth = await _token(db_session, settings, UserRole.ADMIN)

    body = client.post(DRIVERS, json=VALID_DRIVER, headers=auth).json()

    assert body["vettingStatus"] == "pending"
    assert body["isAssignable"] is False


@pytest.mark.parametrize(
    "override",
    [
        {"phone": "123"},
        {"phone": "not-a-phone"},
        {"fullName": "A"},
        {"email": "not-an-email"},
        {"unexpected": "field"},
    ],
)
async def test_invalid_driver_bodies_are_rejected(
    client: TestClient,
    db_session: AsyncSession,
    settings: Settings,
    override: dict[str, str],
) -> None:
    auth = await _token(db_session, settings, UserRole.ADMIN)

    response = client.post(DRIVERS, json={**VALID_DRIVER, **override}, headers=auth)
    assert response.status_code == 422


async def test_a_driver_phone_follows_the_same_rule_as_a_booking(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Same pattern, same digit floor, so the two cannot drift apart."""
    auth = await _token(db_session, settings, UserRole.ADMIN)

    accepted = client.post(
        DRIVERS, json={**VALID_DRIVER, "phone": "+234 800 000 0000"}, headers=auth
    )
    assert accepted.status_code == 201


# --- updates ----------------------------------------------------------------


async def test_a_patch_leaves_omitted_fields_alone(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """The difference between PATCH and PUT, asserted rather than assumed."""
    auth = await _token(db_session, settings, UserRole.ADMIN)
    driver = await _driver(db_session)

    body = client.patch(
        f"{DRIVERS}/{driver.id}", json={"isActive": False}, headers=auth
    ).json()

    assert body["isActive"] is False
    assert body["phone"] == "+234 801 111 1111"
    assert body["vehiclePlate"] == "LAG-123XY"


async def test_deactivating_makes_a_driver_unassignable(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    auth = await _token(db_session, settings, UserRole.ADMIN)
    driver = await _driver(db_session)

    body = client.patch(
        f"{DRIVERS}/{driver.id}", json={"isActive": False}, headers=auth
    ).json()

    assert body["isAssignable"] is False


async def test_verifying_makes_a_driver_assignable(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    auth = await _token(db_session, settings, UserRole.ADMIN)
    driver = await _driver(db_session, vetting_status=VettingStatus.PENDING.value)

    body = client.patch(
        f"{DRIVERS}/{driver.id}", json={"vettingStatus": "verified"}, headers=auth
    ).json()

    assert body["isAssignable"] is True


async def test_updating_an_unknown_driver_is_404(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    auth = await _token(db_session, settings, UserRole.ADMIN)

    response = client.patch(
        f"{DRIVERS}/00000000-0000-0000-0000-000000000000",
        json={"isActive": False},
        headers=auth,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_there_is_no_delete_route(client: TestClient) -> None:
    """Drivers are deactivated, never removed: bookings point at them."""
    response = client.delete(f"{DRIVERS}/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 405


# --- the assignable filter --------------------------------------------------


async def test_assignable_only_returns_exactly_what_assignment_accepts(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """A picker that offers a driver the assign endpoint rejects is worse
    than one that offers nobody. This pins the query to the domain rule."""
    auth = await _token(db_session, settings, UserRole.OPS)
    await _driver(db_session, full_name="Verified Active")
    await _driver(
        db_session,
        full_name="Verified Inactive",
        phone="+234 803 333 3333",
        is_active=False,
    )
    await _driver(
        db_session,
        full_name="Pending Active",
        phone="+234 804 444 4444",
        vetting_status=VettingStatus.PENDING.value,
    )
    await _driver(
        db_session,
        full_name="Rejected Active",
        phone="+234 805 555 5555",
        vetting_status=VettingStatus.REJECTED.value,
    )

    body = client.get(f"{DRIVERS}?assignable_only=true", headers=auth).json()

    assert [row["fullName"] for row in body["items"]] == ["Verified Active"]
    assert all(row["isAssignable"] for row in body["items"])


async def test_the_full_list_still_shows_everyone(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    """Rejected drivers are kept, so the vetting decision stays auditable."""
    auth = await _token(db_session, settings, UserRole.OPS)
    await _driver(db_session)
    await _driver(
        db_session,
        full_name="Rejected Person",
        phone="+234 806 666 6666",
        vetting_status=VettingStatus.REJECTED.value,
    )

    assert client.get(DRIVERS, headers=auth).json()["total"] == 2


async def test_drivers_are_listed_alphabetically(
    client: TestClient, db_session: AsyncSession, settings: Settings
) -> None:
    auth = await _token(db_session, settings, UserRole.OPS)
    await _driver(db_session, full_name="Zoe Zulu")
    await _driver(db_session, full_name="Adam Alpha", phone="+234 807 777 7777")

    names = [
        row["fullName"] for row in client.get(DRIVERS, headers=auth).json()["items"]
    ]
    assert names == sorted(names)


@pytest.mark.parametrize("term", ["Grace", "801 111", "LAG-123"])
async def test_search_matches_name_phone_and_plate(
    client: TestClient, db_session: AsyncSession, settings: Settings, term: str
) -> None:
    auth = await _token(db_session, settings, UserRole.OPS)
    await _driver(db_session)
    await _driver(
        db_session,
        full_name="Someone Else",
        phone="+234 909 999 9999",
        vehicle_plate="XYZ-000AA",
    )

    body = client.get(f"{DRIVERS}?q={term}", headers=auth).json()
    assert [row["fullName"] for row in body["items"]] == ["Grace Hopper"]
