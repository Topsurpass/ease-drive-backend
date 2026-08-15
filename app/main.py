"""Application factory and ASGI entrypoint.

Run with ``uv run uvicorn app.main:app --reload`` or ``fastapi run``.
"""

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import ErrorEnvelopeMiddleware
from app.core.logging import configure_logging
from app.db.session import dispose_engines
from app.schemas.booking import BookingError

logger = logging.getLogger("app.startup")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Announce the resolved configuration, then close pooled connections.

    The shutdown half matters because a reload or redeploy otherwise leaves
    Neon holding connections open until they time out, which eats the
    project's connection budget.

    The startup half exists because both of this app's environment-dependent
    settings fail silently. A missing DATABASE_URL surfaces as a 503 with no
    server-side clue, and a missing EASE_DRIVE_CORS_ORIGINS surfaces in the
    browser as a CORS error while the server logs a perfectly ordinary 200.
    Printing both at boot puts the answer in the platform log before anyone
    has to go looking for it.
    """
    config: Settings = application.state.settings
    logger.info(
        "cors allowlist (%s): %s",
        "from EASE_DRIVE_CORS_ORIGINS" if config.is_cors_configured else "DEFAULT",
        ", ".join(config.cors_origins) or "(empty)",
    )
    if not config.is_cors_configured:
        logger.warning(
            "EASE_DRIVE_CORS_ORIGINS is not set; only the local dev origins are "
            "allowed. A deployed browser client will be blocked by CORS."
        )
    if not config.is_database_configured:
        logger.warning("DATABASE_URL is not set; database routes will return 503.")
    yield
    await dispose_engines()


MAX_REPORTED_ERRORS = 3


def _describe(error: Mapping[str, Any]) -> str:
    """Render one pydantic error as a sentence the form can display."""
    location = error.get("loc") or ()
    parts = [str(part) for part in location if part != "body"]
    field = ".".join(parts) if parts else "request"
    return f"{field}: {error.get('msg', 'is invalid')}"


def _serialisable(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop pydantic's `ctx`, which can hold a raw exception JSON cannot encode."""
    return [
        {key: value for key, value in error.items() if key != "ctx"} for error in errors
    ]


async def validation_exception_handler(
    _: Request, exception: Exception
) -> JSONResponse:
    """Return validation failures in the frontend's `BookingFailure` shape.

    FastAPI's default 422 body is `{"detail": [...]}`, which the booking form
    cannot read. This keeps the discriminated `{ok: false, code, message}`
    contract while preserving the field-level detail for debugging.
    """
    assert isinstance(exception, RequestValidationError)
    errors: Sequence[Mapping[str, Any]] = exception.errors()
    message = (
        "; ".join(_describe(error) for error in errors[:MAX_REPORTED_ERRORS])
        or "The submitted details are not valid."
    )
    body = BookingError(code="validation_error", message=message).model_dump(
        by_alias=True
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=jsonable_encoder({**body, "detail": _serialisable(errors)}),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    A factory rather than a module-level singleton so tests can build an
    isolated app per case without import-order side effects.
    """
    config = settings or get_settings()
    # Before anything logs, or the startup report goes nowhere on a real host.
    configure_logging()
    application = FastAPI(
        title=config.project_name,
        version=config.version,
        openapi_url=f"{config.api_v1_prefix}/openapi.json",
        lifespan=lifespan,
    )
    # `lifespan` gets the application, not the settings, so this is how the
    # resolved config reaches it. Per-app rather than get_settings() so a test
    # that builds an app with explicit Settings logs those, not the process's.
    application.state.settings = config
    # Order matters, and it is inverted: the LAST middleware added is the
    # outermost. ErrorEnvelopeMiddleware goes on first so CORSMiddleware wraps
    # it, which is what lets a 500 leave with its CORS headers attached instead
    # of surfacing in the browser as a bogus CORS failure.
    application.add_middleware(ErrorEnvelopeMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    application.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )
    application.include_router(api_router, prefix=config.api_v1_prefix)
    return application


app: FastAPI = create_app()
