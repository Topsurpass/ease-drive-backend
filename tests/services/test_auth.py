"""Gate tests for sign-in and refresh rotation, against in-memory SQLite.

The rotation cases are the reason this file exists. Rotation with reuse
detection is easy to write and easy to get subtly wrong, and the wrong version
signs people out at random rather than failing loudly — so each branch of
`rotate_refresh_token` is pinned here by name.

SQLite ignores `SELECT ... FOR UPDATE`, so what these prove is the *decision
logic*. The lock itself is proved against Postgres in `tests/integration/`.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import decode_access_token, hash_refresh_token
from app.domain.roles import UserRole
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.auth import (
    AccountDisabledError,
    AccountLockedError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    IssuedTokens,
    authenticate,
    create_user,
    normalise_email,
    revoke_refresh_token,
    rotate_refresh_token,
)

PASSWORD = "a-perfectly-fine-passphrase"


@pytest.fixture
async def user(db_session: AsyncSession) -> User:
    return await create_user(
        db_session,
        email="Ada@Example.com",
        password=PASSWORD,
        full_name="Ada Lovelace",
        role=UserRole.OPS.value,
    )


async def _login(
    session: AsyncSession, settings: Settings, email: str = "ada@example.com"
) -> IssuedTokens:
    return await authenticate(
        session, email=email, password=PASSWORD, settings=settings
    )


# --- accounts ---------------------------------------------------------------


async def test_email_is_stored_lowercased(user: User) -> None:
    """Otherwise Ada@x.com and ada@x.com are two accounts and one of them wins."""
    assert user.email == "ada@example.com"


async def test_password_is_not_stored_in_the_clear(user: User) -> None:
    assert PASSWORD not in user.password_hash
    assert user.password_hash.startswith("$argon2")


@pytest.mark.parametrize(
    "given", ["ada@example.com", "ADA@EXAMPLE.COM", " ada@example.com "]
)
async def test_sign_in_accepts_any_casing_of_the_email(
    db_session: AsyncSession, settings: Settings, user: User, given: str
) -> None:
    assert normalise_email(given) == user.email
    tokens = await _login(db_session, settings, email=given)
    assert tokens.user.id == user.id


async def test_sign_in_rejects_a_wrong_password(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    with pytest.raises(InvalidCredentialsError):
        await authenticate(
            db_session, email=user.email, password="wrong", settings=settings
        )


async def test_sign_in_rejects_an_unknown_email_the_same_way(
    db_session: AsyncSession, settings: Settings
) -> None:
    """Same exception as a wrong password, so the endpoint is not an oracle."""
    with pytest.raises(InvalidCredentialsError):
        await authenticate(
            db_session, email="nobody@example.com", password=PASSWORD, settings=settings
        )


async def test_a_deactivated_account_cannot_sign_in(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    user.is_active = False
    await db_session.commit()

    with pytest.raises(AccountDisabledError):
        await _login(db_session, settings)


async def test_access_token_carries_the_user_id(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    tokens = await _login(db_session, settings)
    assert settings.jwt_secret is not None
    assert decode_access_token(tokens.access_token, settings.jwt_secret) == user.id


# --- lockout ----------------------------------------------------------------


async def test_account_locks_after_the_configured_number_of_failures(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    for _ in range(settings.max_failed_logins):
        with pytest.raises(InvalidCredentialsError):
            await authenticate(
                db_session, email=user.email, password="wrong", settings=settings
            )

    # The right password now fails too: that is the point of a lockout.
    with pytest.raises(AccountLockedError):
        await _login(db_session, settings)


async def test_lockout_reports_when_it_expires(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    for _ in range(settings.max_failed_logins):
        with pytest.raises(InvalidCredentialsError):
            await authenticate(
                db_session, email=user.email, password="wrong", settings=settings
            )

    with pytest.raises(AccountLockedError) as caught:
        await _login(db_session, settings)

    assert caught.value.locked_until > datetime.now(UTC)


async def test_lockout_lifts_once_it_expires(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    tokens = await _login(db_session, settings)
    assert tokens.user.id == user.id


async def test_a_successful_sign_in_clears_the_failure_count(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    with pytest.raises(InvalidCredentialsError):
        await authenticate(
            db_session, email=user.email, password="wrong", settings=settings
        )
    assert user.failed_login_count == 1

    await _login(db_session, settings)
    await db_session.refresh(user)
    assert user.failed_login_count == 0


# --- rotation ---------------------------------------------------------------


async def test_refresh_issues_a_different_token(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    first = await _login(db_session, settings)
    second = await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=settings
    )

    assert second.refresh_token != first.refresh_token
    assert second.user.id == user.id


async def test_refresh_keeps_the_token_in_the_same_family(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    """A family is one sign-in; sign-out has to be able to kill all of it."""
    first = await _login(db_session, settings)
    second = await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=settings
    )

    families = set((await db_session.scalars(select(RefreshToken.family_id))).all())
    assert len(families) == 1
    assert second.refresh_token != first.refresh_token


async def test_an_unknown_refresh_token_is_rejected(
    db_session: AsyncSession, settings: Settings
) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        await rotate_refresh_token(
            db_session, raw_token="not-a-real-token", settings=settings
        )


async def test_an_expired_refresh_token_is_rejected(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    tokens = await _login(db_session, settings)
    record = await db_session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(tokens.refresh_token)
        )
    )
    assert record is not None
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    with pytest.raises(InvalidRefreshTokenError):
        await rotate_refresh_token(
            db_session, raw_token=tokens.refresh_token, settings=settings
        )


# --- the grace window, and why it exists ------------------------------------


async def test_a_replay_inside_the_grace_window_returns_the_same_successor(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    """The single-flight fix.

    Two tabs refresh at once. The loser presents a token the winner already
    spent. Without this branch it would be read as theft and both tabs would be
    signed out; with it, the loser lands on the winner's chain.
    """
    first = await _login(db_session, settings)

    winner = await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=settings
    )
    loser = await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=settings
    )

    assert loser.refresh_token == winner.refresh_token
    assert loser.user.id == user.id


async def test_a_grace_replay_is_idempotent(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    """Ten racing callers must not mint ten tokens."""
    first = await _login(db_session, settings)
    await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=settings
    )

    replays = [
        await rotate_refresh_token(
            db_session, raw_token=first.refresh_token, settings=settings
        )
        for _ in range(10)
    ]

    assert len({token.refresh_token for token in replays}) == 1
    # Two tokens exist in total: the original and its one successor.
    assert len((await db_session.scalars(select(RefreshToken))).all()) == 2


async def test_a_grace_replay_still_returns_a_usable_access_token(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    """The racing caller needs to get on with its request, not just survive."""
    first = await _login(db_session, settings)
    await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=settings
    )
    replay = await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=settings
    )

    assert settings.jwt_secret is not None
    assert decode_access_token(replay.access_token, settings.jwt_secret) == user.id


async def test_reuse_outside_the_grace_window_revokes_the_whole_family(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    """Past the window there is no innocent explanation. Kill the session."""
    first = await _login(db_session, settings)
    second = await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=settings
    )

    # Age the rotation past the grace window.
    record = await db_session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(first.refresh_token)
        )
    )
    assert record is not None
    record.rotated_at = datetime.now(UTC) - timedelta(
        seconds=settings.refresh_grace_seconds + 1
    )
    await db_session.commit()

    with pytest.raises(InvalidRefreshTokenError):
        await rotate_refresh_token(
            db_session, raw_token=first.refresh_token, settings=settings
        )

    # And the *live* token is dead too, which is the whole point: the thief and
    # the victim are both signed out rather than coexisting.
    with pytest.raises(InvalidRefreshTokenError):
        await rotate_refresh_token(
            db_session, raw_token=second.refresh_token, settings=settings
        )


async def test_a_zero_grace_window_makes_any_replay_theft(
    db_session: AsyncSession, user: User, settings: Settings
) -> None:
    """The window is a setting, and setting it to zero restores strict rotation."""
    strict = settings.model_copy(update={"refresh_grace_seconds": 0})

    first = await _login(db_session, strict)
    await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=strict
    )

    with pytest.raises(InvalidRefreshTokenError):
        await rotate_refresh_token(
            db_session, raw_token=first.refresh_token, settings=strict
        )


async def test_refresh_is_refused_once_the_account_is_deactivated(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    """A live refresh token must not outlive the account it belongs to."""
    tokens = await _login(db_session, settings)
    user.is_active = False
    await db_session.commit()

    with pytest.raises(InvalidRefreshTokenError):
        await rotate_refresh_token(
            db_session, raw_token=tokens.refresh_token, settings=settings
        )


# --- sign-out ---------------------------------------------------------------


async def test_sign_out_revokes_the_family(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    first = await _login(db_session, settings)
    second = await rotate_refresh_token(
        db_session, raw_token=first.refresh_token, settings=settings
    )

    await revoke_refresh_token(db_session, raw_token=second.refresh_token)

    with pytest.raises(InvalidRefreshTokenError):
        await rotate_refresh_token(
            db_session, raw_token=second.refresh_token, settings=settings
        )


async def test_sign_out_is_idempotent_and_silent_about_unknown_tokens(
    db_session: AsyncSession,
) -> None:
    """Reporting whether a token existed would let someone test stolen ones."""
    await revoke_refresh_token(db_session, raw_token="never-existed")


async def test_sign_out_does_not_touch_another_session(
    db_session: AsyncSession, settings: Settings, user: User
) -> None:
    """Signing out of one browser must not sign you out of the other."""
    laptop = await _login(db_session, settings)
    phone = await _login(db_session, settings)

    await revoke_refresh_token(db_session, raw_token=laptop.refresh_token)

    still_valid = await rotate_refresh_token(
        db_session, raw_token=phone.refresh_token, settings=settings
    )
    assert still_valid.user.id == user.id
