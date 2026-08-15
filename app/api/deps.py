"""Dependencies shared across endpoints.

Declared as ``Annotated`` aliases so endpoint signatures stay readable and a
dependency can be swapped in tests via ``app.dependency_overrides``.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import TokenError, decode_access_token
from app.db.session import session_scope
from app.domain.roles import UserRole, can_manage_drivers
from app.models.user import User
from app.schemas.auth import AuthError
from app.schemas.booking import BookingError

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_db_session(settings: SettingsDep) -> AsyncIterator[AsyncSession]:
    """Yield a database session, or 503 when no database is configured.

    Returning 503 rather than 500 is the honest status: the request was fine,
    the deployment is missing DATABASE_URL. The body matches the frontend's
    `BookingFailure` shape so the form renders its error state instead of
    choking on an unexpected payload.
    """
    if not settings.is_database_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=BookingError(
                code="unavailable",
                message="The booking service is not configured. Try again shortly.",
            ).model_dump(by_alias=True),
        )

    assert settings.database_url is not None  # narrowed by the guard above
    async for session in session_scope(settings.database_url):
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


# auto_error=False so a missing header reaches our own handler and produces the
# `{ok, code, message}` envelope the console parses, rather than FastAPI's
# `{"detail": "Not authenticated"}`, which the frontend error path cannot read.
_bearer = HTTPBearer(auto_error=False)

CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _unauthorised(message: str, code: str = "invalid_token") -> HTTPException:
    """401 with the auth error envelope and the header the spec requires."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=AuthError(code=code, message=message).model_dump(  # type: ignore[arg-type]
            by_alias=True, mode="json"
        ),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: CredentialsDep, session: SessionDep, settings: SettingsDep
) -> User:
    """Resolve the signed-in staff member, or raise 401.

    The user row is re-read on every request rather than trusted from the
    token's claims. An access token lives for fifteen minutes; without this,
    deactivating an account or demoting an admin would not take effect until it
    expired. Paying one indexed primary-key lookup to make revocation immediate
    is the right trade for a console holding customer contact details.
    """
    if not settings.is_auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AuthError(
                code="unavailable",
                message="Authentication is not configured. Try again shortly.",
            ).model_dump(by_alias=True, mode="json"),
        )

    if credentials is None or not credentials.credentials:
        raise _unauthorised("Sign in to continue.")

    assert settings.jwt_secret is not None  # narrowed by is_auth_configured
    try:
        user_id = decode_access_token(credentials.credentials, settings.jwt_secret)
    except TokenError as error:
        # The reason is deliberately not echoed: "expired" versus "bad
        # signature" tells an attacker which half of the token to work on.
        raise _unauthorised("Your session has expired. Sign in again.") from error

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorised("Your session has expired. Sign in again.")

    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_admin_user(user: CurrentUserDep) -> User:
    """Require an admin, or raise 403.

    403 not 401: the caller is authenticated and re-signing-in will not help,
    which is exactly the distinction the console needs to decide whether to
    redirect to the login page or show "you do not have access".
    """
    if not can_manage_drivers(UserRole(user.role)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=AuthError(
                code="forbidden",
                message="This action needs an administrator account.",
            ).model_dump(by_alias=True, mode="json"),
        )
    return user


AdminUserDep = Annotated[User, Depends(get_admin_user)]
