"""Staff accounts for the ops console.

There is no self-registration anywhere in the API. Accounts are created by
`scripts/create_admin.py` or by an existing admin, because this table is the
only thing standing between the open internet and every customer's phone
number.

`failed_login_count` and `locked_until` implement lockout in the database
rather than in memory. A rate limiter held in a process dies with the process
and is invisible to a second instance, which on a platform that scales to two
containers means no limit at all.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, String, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.roles import UserRole

MAX_EMAIL = 254
MAX_NAME = 80
MAX_ROLE = 16
# argon2 hashes are ~100 chars; the column has room for a longer parameterisation
# or a future algorithm without a migration.
MAX_PASSWORD_HASH = 255

_ROLE_VALUES = ", ".join(f"'{role.value}'" for role in UserRole)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """A member of staff who can sign in to the ops console."""

    __tablename__ = "ease_users"
    __table_args__ = (
        CheckConstraint(f"role IN ({_ROLE_VALUES})", name="ease_users_role_valid"),
        CheckConstraint(
            "failed_login_count >= 0", name="ease_users_failed_login_count_positive"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Stored lowercased by the service. A unique index on the raw value would
    # let Ada@x.com and ada@x.com both exist and only one of them log in.
    email: Mapped[str] = mapped_column(String(MAX_EMAIL), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(MAX_PASSWORD_HASH))
    full_name: Mapped[str] = mapped_column(String(MAX_NAME))
    role: Mapped[str] = mapped_column(String(MAX_ROLE), default=UserRole.OPS.value)

    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    failed_login_count: Mapped[int] = mapped_column(default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        # Deliberately no email: a repr ends up in logs and tracebacks.
        return f"<User {self.id} {self.role}>"
