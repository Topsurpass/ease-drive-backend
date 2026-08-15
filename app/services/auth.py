"""Sign-in, refresh rotation and sign-out. Knows nothing about HTTP.

The interesting part is `rotate_refresh_token`, and specifically why it is not
the naive version.

Refresh-token rotation with reuse detection is the standard defence against a
stolen token: every refresh issues a successor and burns its predecessor, so if
a thief uses a token the real user has already spent, the reuse is visible and
the whole family is revoked. The failure mode nobody warns you about is that
honest clients present a spent token all the time. A page fires four requests
behind an expired access token; two browser tabs wake up together; a serverless
instance retries after a dropped connection. Each of those looks exactly like
theft to a naive implementation, and the user is signed out at random.

The usual answer is a mutex in the client. That answer is wrong, because the
client cannot hold one: two tabs are two JavaScript contexts, and two Vercel
instances are two processes. Correctness cannot live there.

So it lives here, in two parts:

1. The row is locked with ``SELECT ... FOR UPDATE``, so simultaneous refreshes
   serialise in Postgres instead of racing.
2. A token already rotated within ``refresh_grace_seconds`` returns the *same*
   successor it produced the first time, rather than rotating again or crying
   theft. Replaying a spent token inside the grace window is idempotent and
   writes nothing.

Returning the *same* successor is what keeps racing callers on one chain. It
works because successors are derived, not random: see
``app.core.security.derive_successor_token``. The database still holds nothing
but hashes, and the raw successor is recomputed on demand.

Reuse detection keeps its teeth: outside that window there is no innocent
explanation for presenting a spent token, and the family dies. A thief must
race the real user inside a 30-second window, and lands on the same shared
token — so the next rotation by either party turns the other into a detectable
reuse.

SQLite ignores ``FOR UPDATE``, so the gate lane proves the grace-window logic
and the Neon-backed ``integration`` lane proves the locking.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    derive_successor_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User


class AuthError(Exception):
    """Sign-in or refresh failed. Carries no detail the caller should echo."""


class InvalidCredentialsError(AuthError):
    """Wrong email or wrong password. Deliberately indistinguishable."""


class AccountLockedError(AuthError):
    """Too many failed attempts; the account is temporarily locked."""

    def __init__(self, locked_until: datetime) -> None:
        super().__init__("account locked")
        self.locked_until = locked_until


class AccountDisabledError(AuthError):
    """The account exists but has been deactivated."""


class InvalidRefreshTokenError(AuthError):
    """The refresh token is unknown, expired, or revoked."""


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    """What a successful sign-in or refresh hands back."""

    access_token: str
    refresh_token: str
    expires_in: int
    user: User


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Attach UTC to a naive timestamp.

    SQLite returns naive datetimes even from a `DateTime(timezone=True)`
    column, so comparing a stored value against an aware `now()` raises
    `TypeError: can't compare offset-naive and offset-aware datetimes`. The
    gate lane runs on SQLite; without this, every expiry check would blow up
    there and pass on Postgres.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def normalise_email(email: str) -> str:
    """Lowercase and strip, so one person cannot own two accounts."""
    return email.strip().lower()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    role: str,
) -> User:
    """Create a staff account. Used by the seed script and by admins."""
    user = User(
        email=normalise_email(email),
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        role=role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _sign_access(user: User, settings: Settings) -> str:
    """Sign an access token for `user`."""
    assert settings.jwt_secret is not None  # guarded by the caller
    return create_access_token(
        user_id=user.id,
        role=user.role,
        secret=settings.jwt_secret,
        ttl_seconds=settings.access_token_ttl_seconds,
    )


async def _issue(
    session: AsyncSession,
    user: User,
    settings: Settings,
    *,
    raw_refresh: str,
    family_id: uuid.UUID,
) -> IssuedTokens:
    """Record a refresh token's hash and pair it with a fresh access token."""
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            family_id=family_id,
            expires_at=_utcnow()
            + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    return IssuedTokens(
        access_token=_sign_access(user, settings),
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_seconds,
        user=user,
    )


async def authenticate(
    session: AsyncSession, *, email: str, password: str, settings: Settings
) -> IssuedTokens:
    """Verify credentials and issue a new token family.

    Lockout counters live on the user row rather than in process memory: an
    in-memory limiter is per-instance, so a platform running two containers
    doubles the allowance, and a redeploy resets it to zero.
    """
    user = await session.scalar(
        select(User).where(User.email == normalise_email(email))
    )

    if user is None:
        # Hash anyway. Returning immediately makes "no such user" measurably
        # faster than "wrong password", which turns response time into a user
        # enumeration oracle.
        hash_password(password)
        raise InvalidCredentialsError

    if user.locked_until is not None and _as_utc(user.locked_until) > _utcnow():
        raise AccountLockedError(_as_utc(user.locked_until))

    if not verify_password(password, user.password_hash):
        await _record_failed_login(session, user, settings)
        raise InvalidCredentialsError

    if not user.is_active:
        # Checked after the password, so a disabled account cannot be
        # distinguished from a wrong password without the right one.
        raise AccountDisabledError

    user.failed_login_count = 0
    user.locked_until = None

    # A sign-in starts a new family, so its first token is random. Every token
    # after it in the chain is derived — see `derive_successor_token`.
    tokens = await _issue(
        session,
        user,
        settings,
        raw_refresh=create_refresh_token(),
        family_id=uuid.uuid4(),
    )
    await session.commit()
    await session.refresh(user)
    return tokens


async def _record_failed_login(
    session: AsyncSession, user: User, settings: Settings
) -> None:
    """Count the failure and lock the account once the limit is reached."""
    user.failed_login_count += 1
    if user.failed_login_count >= settings.max_failed_logins:
        user.locked_until = _utcnow() + timedelta(seconds=settings.lockout_seconds)
        user.failed_login_count = 0
    await session.commit()


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    """Revoke every live token descended from one sign-in."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_utcnow())
    )


async def rotate_refresh_token(
    session: AsyncSession, *, raw_token: str, settings: Settings
) -> IssuedTokens:
    """Exchange a refresh token for a new pair. See the module docstring."""
    token_hash = hash_refresh_token(raw_token)

    # FOR UPDATE is what serialises two simultaneous refreshes of the same
    # token. Without it both read "not yet rotated", both rotate, and the
    # second one's successor orphans the first.
    record = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .with_for_update()
    )

    if record is None:
        raise InvalidRefreshTokenError

    now = _utcnow()

    if _as_utc(record.expires_at) <= now:
        raise InvalidRefreshTokenError

    assert settings.jwt_secret is not None  # guarded by the caller

    if record.rotated_at is not None:
        # Already spent. Either an honest race or a stolen token; the clock
        # decides which.
        within_grace = (
            _as_utc(record.rotated_at)
            + timedelta(seconds=settings.refresh_grace_seconds)
            > now
        )
        if within_grace:
            return await _replay_rotation(session, record, settings)

        await _revoke_family(session, record.family_id)
        await session.commit()
        raise InvalidRefreshTokenError

    if record.revoked_at is not None:
        # Revoked without being rotated: an explicit sign-out, or a family
        # already killed by a reuse elsewhere. Not replayable.
        raise InvalidRefreshTokenError

    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise InvalidRefreshTokenError

    successor = derive_successor_token(record.token_hash, settings.jwt_secret)
    tokens = await _issue(
        session,
        user,
        settings,
        raw_refresh=successor,
        family_id=record.family_id,
    )

    record.rotated_at = now
    record.revoked_at = now
    record.replaced_by_hash = hash_refresh_token(successor)

    await session.commit()
    await session.refresh(user)
    return tokens


async def _replay_rotation(
    session: AsyncSession, record: RefreshToken, settings: Settings
) -> IssuedTokens:
    """Re-answer a rotation that already happened, inside the grace window.

    Recomputes the same successor the winning call minted, so a racing caller
    ends up on the same chain rather than a divergent one. Idempotent: calling
    it ten times inside the window returns the same refresh token every time
    and writes nothing.

    A thief racing the real user therefore lands on the *shared* token. The
    moment either party rotates it, the other's next use is a reuse outside the
    window, and the family dies — so theft still converges on detection instead
    of quietly persisting on a parallel chain.
    """
    assert settings.jwt_secret is not None

    successor_raw = derive_successor_token(record.token_hash, settings.jwt_secret)
    successor = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(successor_raw)
        )
    )
    if successor is None or successor.revoked_at is not None:
        # The successor was itself spent or revoked in the meantime, so there
        # is no live token to hand back and the caller must sign in again.
        raise InvalidRefreshTokenError

    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise InvalidRefreshTokenError

    return IssuedTokens(
        access_token=_sign_access(user, settings),
        refresh_token=successor_raw,
        expires_in=settings.access_token_ttl_seconds,
        user=user,
    )


async def revoke_refresh_token(session: AsyncSession, *, raw_token: str) -> None:
    """Sign out: revoke the presented token's whole family.

    The family, not just the token, because a sign-out that leaves a sibling
    token alive is not a sign-out.
    """
    record = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(raw_token)
        )
    )
    if record is None:
        # Already gone. Sign-out is idempotent and must not report whether the
        # token was real.
        return

    await _revoke_family(session, record.family_id)
    await session.commit()
