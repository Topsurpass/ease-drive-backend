"""Sign-in, refresh, sign-out and "who am I".

These are called by the Next.js route handlers in
`ease-drive-frontend/src/app/api/auth/`, not by the browser directly. That
indirection exists so the refresh cookie is set on the frontend's own origin:
the API and the console are on different registrable domains, and a cookie set
here would be a third-party cookie, which Safari blocks outright and Firefox
partitions. See the frontend's `src/app/api/auth/README.md`.

`GET /auth/me` is the exception and is called with a bearer token like every
other console endpoint.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUserDep, SessionDep, SettingsDep
from app.schemas.auth import (
    AuthError,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserProfile,
)
from app.services.auth import (
    AccountDisabledError,
    AccountLockedError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    IssuedTokens,
    authenticate,
    revoke_refresh_token,
    rotate_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

#: OpenAPI response declarations. Typed explicitly because FastAPI's `responses`
#: parameter is keyed by `int | str`, and a bare dict literal infers `int`.
_Responses = dict[int | str, dict[str, Any]]

_UNAUTHORISED: _Responses = {status.HTTP_401_UNAUTHORIZED: {"model": AuthError}}
_UNAVAILABLE: _Responses = {status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuthError}}
_LOCKED: _Responses = {status.HTTP_423_LOCKED: {"model": AuthError}}


def _error(status_code: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=AuthError(code=code, message=message, **extra).model_dump(  # type: ignore[arg-type]
            by_alias=True, mode="json"
        ),
    )


def _require_auth_configured(settings: SettingsDep) -> None:
    """503 when no signing key is set, rather than a 500 from deep inside."""
    if not settings.is_auth_configured:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "unavailable",
            "Authentication is not configured. Try again shortly.",
        )


def _to_response(tokens: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        user=UserProfile(
            id=str(tokens.user.id),
            email=tokens.user.email,
            full_name=tokens.user.full_name,
            role=tokens.user.role,  # type: ignore[arg-type]
        ),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Sign in to the ops console",
    responses={**_UNAUTHORISED, **_UNAVAILABLE, **_LOCKED},
)
async def login(
    request: LoginRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    """Exchange credentials for an access/refresh pair."""
    _require_auth_configured(settings)

    try:
        tokens = await authenticate(
            session, email=request.email, password=request.password, settings=settings
        )
    except AccountLockedError as error:
        # 423 rather than 401 so the console can say "locked" specifically.
        # Safe to disclose: the caller already failed five times, so this
        # reveals nothing they could not infer.
        raise _error(
            status.HTTP_423_LOCKED,
            "account_locked",
            "Too many failed attempts. This account is temporarily locked.",
            locked_until=error.locked_until,
        ) from error
    except AccountDisabledError as error:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "account_disabled",
            "This account has been deactivated. Ask an administrator.",
        ) from error
    except InvalidCredentialsError as error:
        # One message for "no such user" and "wrong password" alike, so the
        # endpoint cannot be used to discover who has an account.
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_credentials",
            "That email and password do not match.",
        ) from error

    return _to_response(tokens)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new pair",
    responses={**_UNAUTHORISED, **_UNAVAILABLE},
)
async def refresh(
    request: RefreshRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    """Rotate a refresh token.

    Idempotent inside the grace window: replaying the same token returns the
    same successor rather than failing. `app.services.auth` explains why that
    is what makes rotation survivable in a browser.
    """
    _require_auth_configured(settings)

    try:
        tokens = await rotate_refresh_token(
            session, raw_token=request.refresh_token, settings=settings
        )
    except InvalidRefreshTokenError as error:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_token",
            "Your session has expired. Sign in again.",
        ) from error

    return _to_response(tokens)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out and revoke the session",
)
async def logout(request: RefreshRequest, session: SessionDep) -> None:
    """Revoke the token's whole family.

    Always 204, even for a token that was never valid. Sign-out is idempotent,
    and reporting whether the token existed would turn this into an oracle for
    checking stolen tokens.
    """
    await revoke_refresh_token(session, raw_token=request.refresh_token)


@router.get(
    "/me",
    response_model=UserProfile,
    summary="The signed-in staff member",
    responses=_UNAUTHORISED,
)
async def me(user: CurrentUserDep) -> UserProfile:
    """Return the current user, re-read from the database on every call."""
    return UserProfile(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,  # type: ignore[arg-type]
    )
