"""Application factory and ASGI entrypoint.

Run with ``uv run uvicorn app.main:app --reload`` or ``fastapi run``.
"""

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
from app.db.session import dispose_engines
from app.schemas.booking import BookingError


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Close pooled database connections on shutdown.

    Without this, a reload or redeploy leaves Neon holding connections open
    until they time out, which eats the project's connection budget.
    """
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
    application = FastAPI(
        title=config.project_name,
        version=config.version,
        openapi_url=f"{config.api_v1_prefix}/openapi.json",
        lifespan=lifespan,
    )
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
