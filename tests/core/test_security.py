"""Gate tests for hashing and token primitives.

`tests/conftest.py` weakens Argon2 for the rest of the suite so the gate stays
fast. This module deliberately reaches past that and builds the *production*
hasher, so the parameters that actually ship are exercised — once — rather than
being taken on trust.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import (
    ARGON2_MEMORY_COST_KIB,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    TokenError,
    _build_password_hasher,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    derive_successor_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

SECRET = "a-test-signing-key-long-enough-for-hs256"
OTHER_SECRET = "a-different-key-also-long-enough-for-hs256"


# --- passwords --------------------------------------------------------------


def test_production_hasher_round_trips() -> None:
    """The parameters that ship, not the weakened test ones."""
    hasher = _build_password_hasher()
    stored = hasher.hash("correct horse battery staple")

    assert hasher.verify("correct horse battery staple", stored)
    assert not hasher.verify("wrong horse battery staple", stored)


def test_production_parameters_meet_the_owasp_floor() -> None:
    """Guards against someone tuning these down to make a slow test pass.

    OWASP's Password Storage Cheat Sheet for Argon2id: m >= 19456 KiB, t >= 2,
    p >= 1. Lowering any of them is a security decision, and it should have to
    be made deliberately, against a failing test.
    """
    assert ARGON2_MEMORY_COST_KIB >= 19456
    assert ARGON2_TIME_COST >= 2
    assert ARGON2_PARALLELISM >= 1


def test_hashes_are_salted() -> None:
    """The same password twice must not produce the same hash."""
    assert hash_password("same-password") != hash_password("same-password")


def test_hash_does_not_contain_the_password() -> None:
    assert "hunter2hunter2" not in hash_password("hunter2hunter2")


def test_verify_returns_false_for_a_malformed_hash() -> None:
    """A corrupt row is a failed sign-in, not a 500 and an outage."""
    assert verify_password("anything", "not-a-real-hash") is False


def test_verify_returns_false_for_an_empty_hash() -> None:
    assert verify_password("anything", "") is False


# --- access tokens ----------------------------------------------------------


def test_access_token_round_trips() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(
        user_id=user_id, role="admin", secret=SECRET, ttl_seconds=900
    )
    assert decode_access_token(token, SECRET) == user_id


def test_a_token_signed_with_another_key_is_rejected() -> None:
    token = create_access_token(
        user_id=uuid.uuid4(), role="ops", secret=SECRET, ttl_seconds=900
    )
    with pytest.raises(TokenError):
        decode_access_token(token, OTHER_SECRET)


def test_an_expired_token_is_rejected() -> None:
    token = create_access_token(
        user_id=uuid.uuid4(),
        role="ops",
        secret=SECRET,
        ttl_seconds=60,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    with pytest.raises(TokenError):
        decode_access_token(token, SECRET)


@pytest.mark.parametrize("garbage", ["", "not.a.token", "a.b.c", "..."])
def test_malformed_tokens_are_rejected(garbage: str) -> None:
    with pytest.raises(TokenError):
        decode_access_token(garbage, SECRET)


def test_tokens_are_unique_per_issue() -> None:
    """The `jti` claim means two tokens for one user are still distinguishable."""
    user_id = uuid.uuid4()
    first = create_access_token(
        user_id=user_id, role="ops", secret=SECRET, ttl_seconds=900
    )
    second = create_access_token(
        user_id=user_id, role="ops", secret=SECRET, ttl_seconds=900
    )
    assert first != second


def test_an_unsigned_token_is_rejected() -> None:
    """The `alg: none` attack: a token that declares it needs no signature."""
    import jwt

    forged = jwt.encode({"sub": str(uuid.uuid4())}, key="", algorithm="none")
    with pytest.raises(TokenError):
        decode_access_token(forged, SECRET)


# --- refresh tokens ---------------------------------------------------------


def test_refresh_tokens_are_unique() -> None:
    assert create_refresh_token() != create_refresh_token()


def test_refresh_hash_is_stable_and_one_way() -> None:
    token = create_refresh_token()
    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert token not in hash_refresh_token(token)
    assert len(hash_refresh_token(token)) == 64


def test_successor_is_deterministic() -> None:
    """What makes a grace-window replay able to return the same token twice."""
    predecessor = hash_refresh_token(create_refresh_token())
    assert derive_successor_token(predecessor, SECRET) == derive_successor_token(
        predecessor, SECRET
    )


def test_successor_depends_on_the_secret() -> None:
    """Holding a spent token must not be enough to compute what came next."""
    predecessor = hash_refresh_token(create_refresh_token())
    assert derive_successor_token(predecessor, SECRET) != derive_successor_token(
        predecessor, OTHER_SECRET
    )


def test_successor_differs_per_predecessor() -> None:
    first = hash_refresh_token(create_refresh_token())
    second = hash_refresh_token(create_refresh_token())
    assert derive_successor_token(first, SECRET) != derive_successor_token(
        second, SECRET
    )


def test_the_chain_advances() -> None:
    """Each link must differ from the one before, or rotation is a no-op."""
    first = create_refresh_token()
    second = derive_successor_token(hash_refresh_token(first), SECRET)
    third = derive_successor_token(hash_refresh_token(second), SECRET)

    assert len({first, second, third}) == 3
