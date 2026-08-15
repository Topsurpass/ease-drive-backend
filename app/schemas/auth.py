"""Auth wire contract.

camelCase on the wire, matching `app.schemas.booking` and the zod schemas in
`ease-drive-frontend/src/lib/validators/`.

The refresh token never appears in a response body. It is returned to the
Next.js route handler, which puts it in an HttpOnly cookie on its own origin;
a token in a JSON body is a token any script on the page can read.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel

from app.domain.roles import UserRole

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


class CamelModel(BaseModel):
    """Base for every auth schema: camelCase out, either case in."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class LoginRequest(CamelModel):
    """Credentials posted by the console's sign-in form."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        # Passwords are NOT stripped: leading or trailing whitespace is a
        # legitimate part of a passphrase, and silently trimming it would make
        # a password that was accepted at creation fail at sign-in.
        str_strip_whitespace=False,
    )

    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserProfile(CamelModel):
    """The signed-in user, as the console needs to render them."""

    id: str
    email: EmailStr
    full_name: str
    role: UserRole


class TokenResponse(CamelModel):
    """What sign-in and refresh return.

    `refresh_token` is present because the *caller* is the Next.js route
    handler, not the browser. It is stripped before anything reaches the page.
    """

    access_token: str
    refresh_token: str
    #: Seconds until `access_token` expires, so the client can refresh ahead of
    #: a 401 rather than after one.
    expires_in: int
    user: UserProfile


class RefreshRequest(CamelModel):
    """The refresh token, read from the cookie by the route handler."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )

    refresh_token: str = Field(min_length=1)


class AuthError(CamelModel):
    """Failure body, discriminated the same way `BookingError` is."""

    ok: Literal[False] = False
    code: Literal[
        "invalid_credentials",
        "account_locked",
        "account_disabled",
        "invalid_token",
        "forbidden",
        "unavailable",
    ]
    message: str
    #: Set only for `account_locked`, so the console can say how long is left
    #: rather than "try again later".
    locked_until: datetime | None = None


class CreateUserRequest(CamelModel):
    """Used by the seed script and by an admin creating staff accounts."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )

    email: EmailStr
    # Length is the only rule enforced. Composition rules ("one uppercase, one
    # symbol") measurably push people toward Password1! and are no longer
    # recommended by NIST SP 800-63B.
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )
    full_name: str = Field(min_length=2, max_length=80)
    role: UserRole = UserRole.OPS
