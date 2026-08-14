"""Application factory and ASGI entrypoint."""

from fastapi import FastAPI

from api.routes import hello_router


def create_app() -> FastAPI:
    """Build a configured FastAPI application.

    A factory rather than a module-level singleton so tests can build an
    isolated app per case without import-order side effects.
    """
    application = FastAPI(
        title="Ease Drive API",
        version="0.1.0",
        summary="HTTP API for Ease Drive.",
    )
    application.include_router(hello_router)
    return application


app: FastAPI = create_app()
