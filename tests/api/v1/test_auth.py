"""Gate tests for the auth endpoints.

What the service does is covered in `tests/services/test_auth.py`. What this
file covers is the HTTP contract the console depends on: status codes, the
`{ok, code, message}` envelope, and the promise that no response ever explains
*why* a sign-in failed in a way that could be used to enumerate accounts.
"""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.domain.roles import UserRole
from app.main import create_app
from app.models.user import User
from app.services.auth import create_user

PASSWORD = "a-perfectly-fine-passphrase"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


@pytest.fixture
async def staff(db_session: AsyncSession) -> User:
    return await create_user(
        db_session,
        email="ada@example.com",
        password=PASSWORD,
        full_name="Ada Lovelace",
        role=UserRole.OPS.value,
    )


@pytest.fixture
def unconfigured_client(db_session: AsyncSession) -> Iterator[TestClient]:
    """A client whose settings carry no signing key."""
    settings = Settings(_env_file=None, jwt_secret=None)  # type: ignore[call-arg]
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _session_override
    with TestClient(application) as client:
        yield client
    application.dependency_overrides.clear()


def _login(client: TestClient, **overrides: Any) -> Response:
    body = {"email": "ada@example.com", "password": PASSWORD}
    body.update(overrides)
    return client.post(LOGIN, json=body)


# --- sign-in ----------------------------------------------------------------


def test_login_returns_tokens_and_the_profile(
    db_client: TestClient, staff: User
) -> None:
    response = _login(db_client)

    assert response.status_code == 200
    body = response.json()
    assert body["accessToken"]
    assert body["refreshToken"]
    assert body["expiresIn"] > 0
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["role"] == "ops"
    assert body["user"]["fullName"] == "Ada Lovelace"


def test_login_never_returns_the_password_hash(
    db_client: TestClient, staff: User
) -> None:
    assert "passwordHash" not in _login(db_client).text
    assert "password_hash" not in _login(db_client).text


def test_a_wrong_password_is_401_with_the_error_envelope(
    db_client: TestClient, staff: User
) -> None:
    response = _login(db_client, password="wrong")

    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "invalid_credentials"
    assert body["message"]


def test_an_unknown_account_is_indistinguishable_from_a_wrong_password(
    db_client: TestClient, staff: User
) -> None:
    """If these differed, the endpoint would list who works here."""
    unknown = _login(db_client, email="nobody@example.com")
    wrong = _login(db_client, password="wrong")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["code"] == wrong.json()["code"]
    assert unknown.json()["message"] == wrong.json()["message"]


def test_lockout_returns_423_and_says_when_it_lifts(
    db_client: TestClient, staff: User, settings: Settings
) -> None:
    for _ in range(settings.max_failed_logins):
        _login(db_client, password="wrong")

    response = _login(db_client)

    assert response.status_code == 423
    body = response.json()
    assert body["code"] == "account_locked"
    assert body["lockedUntil"] is not None


def test_a_deactivated_account_is_401_account_disabled(
    db_client: TestClient, db_session: AsyncSession, staff: User
) -> None:
    staff.is_active = False

    response = _login(db_client)

    assert response.status_code == 401
    assert response.json()["code"] == "account_disabled"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "ada@example.com"},
        {"password": PASSWORD},
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "ada@example.com", "password": PASSWORD, "extra": "field"},
    ],
)
def test_a_malformed_login_body_is_422(
    db_client: TestClient, staff: User, body: dict[str, str]
) -> None:
    assert db_client.post(LOGIN, json=body).status_code == 422


def test_login_is_503_when_no_signing_key_is_configured(
    unconfigured_client: TestClient, staff: User
) -> None:
    """The honest status: the request was fine, the deployment is not."""
    response = _login(unconfigured_client)

    assert response.status_code == 503
    assert response.json()["code"] == "unavailable"


# --- refresh ----------------------------------------------------------------


def test_refresh_exchanges_the_token(db_client: TestClient, staff: User) -> None:
    first = _login(db_client).json()

    response = db_client.post(REFRESH, json={"refreshToken": first["refreshToken"]})

    assert response.status_code == 200
    assert response.json()["refreshToken"] != first["refreshToken"]
    assert response.json()["user"]["email"] == "ada@example.com"


def test_refresh_is_idempotent_inside_the_grace_window(
    db_client: TestClient, staff: User
) -> None:
    """Two tabs refreshing together must both keep working."""
    first = _login(db_client).json()

    winner = db_client.post(REFRESH, json={"refreshToken": first["refreshToken"]})
    loser = db_client.post(REFRESH, json={"refreshToken": first["refreshToken"]})

    assert winner.status_code == loser.status_code == 200
    assert winner.json()["refreshToken"] == loser.json()["refreshToken"]


def test_an_unknown_refresh_token_is_401(db_client: TestClient) -> None:
    response = db_client.post(REFRESH, json={"refreshToken": "nope"})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


# --- sign-out ---------------------------------------------------------------


def test_logout_revokes_the_session(db_client: TestClient, staff: User) -> None:
    tokens = _login(db_client).json()

    assert (
        db_client.post(
            LOGOUT, json={"refreshToken": tokens["refreshToken"]}
        ).status_code
        == 204
    )

    replayed = db_client.post(REFRESH, json={"refreshToken": tokens["refreshToken"]})
    assert replayed.status_code == 401


def test_logout_is_204_even_for_a_token_that_never_existed(
    db_client: TestClient,
) -> None:
    """Otherwise it becomes an oracle for testing stolen tokens."""
    response = db_client.post(LOGOUT, json={"refreshToken": "never-existed"})
    assert response.status_code == 204


# --- who am I ---------------------------------------------------------------


def test_me_returns_the_signed_in_user(db_client: TestClient, staff: User) -> None:
    token = _login(db_client).json()["accessToken"]

    response = db_client.get(ME, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"


def test_me_without_a_token_is_401(db_client: TestClient) -> None:
    response = db_client.get(ME)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    ["Bearer not-a-token", "Bearer ", "Basic abc123", "garbage"],
)
def test_me_with_a_bad_authorization_header_is_401(
    db_client: TestClient, header: str
) -> None:
    response = db_client.get(ME, headers={"Authorization": header})
    assert response.status_code == 401


def test_me_reflects_a_deactivation_immediately(
    db_client: TestClient, db_session: AsyncSession, staff: User
) -> None:
    """The user row is re-read per request, so revocation cannot lag a token."""
    token = _login(db_client).json()["accessToken"]
    assert (
        db_client.get(ME, headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )

    staff.is_active = False

    assert (
        db_client.get(ME, headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )
