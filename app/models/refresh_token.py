"""Refresh tokens, with rotation and reuse detection.

The token itself is never stored. Only its SHA-256 hash is, so a dump of this
table cannot be replayed against the API — the same reason password hashes are
stored instead of passwords. Lookups hash the presented token and query by
that.

Three columns carry the security design:

``family_id``
    Every token descended from one login shares it. Detecting reuse revokes the
    whole family, which ends the session for the thief *and* the victim rather
    than letting a stolen token quietly coexist with the real one.

``replaced_by_hash`` / ``rotated_at``
    What makes an honest race survivable. Two tabs can present the same token
    at the same moment; the loser finds the row already rotated and, inside
    ``REFRESH_GRACE_SECONDS``, is handed the same successor rather than being
    treated as an attacker. Outside that window there is no innocent
    explanation, and the family is revoked.

Without those two columns, correctness would rest on the browser never issuing
two refreshes at once — which it does, routinely, the moment a page fires
several requests behind an expired access token.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# hex sha256
TOKEN_HASH_LENGTH = 64


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RefreshToken(Base):
    """One issued refresh token, live or spent."""

    __tablename__ = "ease_refresh_tokens"
    __table_args__ = (
        Index("ease_refresh_tokens_family_id_idx", "family_id"),
        Index("ease_refresh_tokens_user_id_idx", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ease_users.id", ondelete="CASCADE")
    )

    token_hash: Mapped[str] = mapped_column(
        String(TOKEN_HASH_LENGTH), unique=True, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    #: Set when this token is spent or when its family is revoked.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set only when spent by a normal rotation, alongside `replaced_by_hash`.
    #: Distinct from `revoked_at`, which is also set by a family revocation
    #: where there is no successor to replay.
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replaced_by_hash: Mapped[str | None] = mapped_column(
        String(TOKEN_HASH_LENGTH), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    def __repr__(self) -> str:
        state = "revoked" if self.revoked_at else "live"
        return f"<RefreshToken {self.id} {state}>"
