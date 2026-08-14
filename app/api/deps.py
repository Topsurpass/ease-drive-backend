"""Dependencies shared across endpoints.

Declared as ``Annotated`` aliases so endpoint signatures stay readable and a
dependency can be swapped in tests via ``app.dependency_overrides``.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import session_scope
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
