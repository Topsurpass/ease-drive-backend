"""Application factory and ASGI entrypoint.

Run with ``uv run uvicorn app.main:app --reload``.
"""

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings


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
    )
    application.include_router(api_router, prefix=config.api_v1_prefix)
    return application


app: FastAPI = create_app()
