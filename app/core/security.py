"""Password hashing, access-token signing, and refresh-token hashing.

Three separate jobs that all get called "security", kept apart here because
they have different requirements:

*Passwords* need a deliberately slow, memory-hard hash. Argon2id, via pwdlib —
the library FastAPI's own docs moved to after passlib went quiet.

*Access tokens* need to be verifiable without a database round trip and to
expire on their own. A JWT signed HS256 with a symmetric secret; there is one
service issuing and verifying, so asymmetric keys would add key distribution
for no gain.

*Refresh tokens* need to be unusable to anyone who reads the database. They are
random, never stored, and matched by SHA-256 — a plain fast hash on purpose,
because unlike a password the input already has 256 bits of entropy and a slow
KDF would only add latency to every refresh.
"""

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

# Argon2id parameters, set explicitly rather than taking the library default.
#
# argon2-cffi defaults to m=64 MiB, t=3, p=4 (RFC 9106's first option). Those
# are tuned for a machine with cores and memory to spare; on the container this
# API actually runs in they cost ~1.4s and 64 MiB *per concurrent sign-in*,
# which is both a poor login experience and a cheap way to exhaust the host.
#
# These are OWASP's current Password Storage Cheat Sheet recommendation for
# Argon2id and RFC 9106's second option: 19 MiB, t=2, p=1. Measured ~650ms on a
# slow WSL2 laptop, well under 100ms on server hardware — comfortably in the
# range that makes offline cracking expensive without making sign-in feel
# broken.
#
# Raise these when the host gets bigger. pwdlib records the parameters inside
# each hash, so old hashes keep verifying and only new ones use new settings.
ARGON2_TIME_COST: Final[int] = 2
ARGON2_MEMORY_COST_KIB: Final[int] = 19456
ARGON2_PARALLELISM: Final[int] = 1


def _build_password_hasher() -> PasswordHash:
    """Return the production hasher."""
    return PasswordHash(
        (
            Argon2Hasher(
                time_cost=ARGON2_TIME_COST,
                memory_cost=ARGON2_MEMORY_COST_KIB,
                parallelism=ARGON2_PARALLELISM,
            ),
        )
    )


_password_hash = _build_password_hasher()


def set_password_hasher(hasher: PasswordHash) -> None:
    """Replace the process-wide hasher.

    Exists for one caller: the test suite, which swaps in deliberately weak
    parameters so the gate lane stays under its time budget instead of spending
    a second and a half per fixture. `tests/core/test_security.py` exercises the
    real parameters once, so the production configuration is still proved.

    Never call this from application code.
    """
    global _password_hash
    _password_hash = hasher


ALGORITHM: Final[str] = "HS256"

#: 256 bits, url-safe. `token_urlsafe(32)` yields 43 characters.
REFRESH_TOKEN_BYTES: Final[int] = 32


class TokenError(Exception):
    """An access token was missing, malformed, expired or wrongly signed.

    One exception for every failure mode on purpose: telling a caller *why*
    their token failed tells an attacker the same thing.
    """


def hash_password(password: str) -> str:
    """Return an Argon2id hash of `password`."""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True when `password` matches `password_hash`.

    Never raises. A malformed or truncated hash in the database is a failed
    verification, not a 500 — otherwise a corrupt row becomes an outage.
    """
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        return False


def create_access_token(
    *,
    user_id: uuid.UUID,
    role: str,
    secret: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> str:
    """Sign a short-lived access token.

    `role` is embedded so an authorization check does not need a second query,
    but it is only ever a hint: `app.api.deps` re-reads the user, because a
    role revoked thirty seconds ago must not still be honoured for the
    remaining life of a token.
    """
    issued = now or datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(seconds=ttl_seconds)).timestamp()),
        # A unique id per token, so one can be denylisted individually if that
        # ever becomes necessary.
        "jti": secrets.token_urlsafe(8),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str) -> uuid.UUID:
    """Return the subject of a valid token, or raise `TokenError`.

    `algorithms` is pinned to a single entry. Accepting a list the token itself
    chooses from is how the classic "alg: none" and RS256-to-HS256 confusion
    attacks work.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as error:
        raise TokenError(str(error)) from error

    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise TokenError("token has no subject")

    try:
        return uuid.UUID(subject)
    except ValueError as error:
        raise TokenError("token subject is not a user id") from error


def create_refresh_token() -> str:
    """Return a fresh, random refresh token to start a new family.

    The value is returned once and never stored; only `hash_refresh_token` of
    it goes to the database.
    """
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def derive_successor_token(predecessor_hash: str, secret: str) -> str:
    """Return the token that must follow `predecessor_hash` in its chain.

    Successors are derived rather than random so that a refresh which already
    happened can be answered again — identically — without keeping a usable
    token at rest. The database still stores only hashes; the raw successor is
    recomputed on demand from the predecessor's hash and the server secret.

    That is what makes the grace window in `app.services.auth` possible. The
    alternative implementations are both worse: storing the raw successor for
    30 seconds puts a live bearer credential in the table, and handing the
    caller a *different* new token would leave two racing tabs on divergent
    chains, so whichever one refreshed second would later look like theft.

    Unpredictable without `secret` — an attacker holding a spent token cannot
    compute what came next. If `secret` leaks, access tokens can be forged
    directly, so this adds no new exposure.
    """
    digest = hmac.new(
        secret.encode("utf-8"),
        b"ease-drive:refresh-successor:" + predecessor_hash.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def hash_refresh_token(token: str) -> str:
    """Return the lookup hash for a refresh token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
