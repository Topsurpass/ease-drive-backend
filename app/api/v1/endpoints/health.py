"""Deployment diagnostic.

Ported from nibbs-report's `/api/health`. Purpose is the same: tell a 503
caused by a missing or misscoped DATABASE_URL apart from one caused by an
unreachable database. Reports the host and database name, never the password.
"""

import asyncio
import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import SettingsDep
from app.db.session import get_sessionmaker
from app.db.url import describe_database_url

router = APIRouter(tags=["health"])

# A hung check is worse than a failed one, so the probe is capped rather than
# left to the driver's own timeout. 15s, not the 8s nibbs-report uses: a cold
# Neon compute was measured here at 7967ms, which would report a healthy
# database as timed out on the first request after an idle period.
PROBE_TIMEOUT_SECONDS = 15.0


class DatabaseHealth(BaseModel):
    configured: bool
    host: str | None = None
    database: str | None = None
    pooled: str | None = None
    ok: bool = False
    latency_ms: float | None = None
    error: str | None = None


class CorsHealth(BaseModel):
    """What the browser is actually allowed to do, checkable over HTTP.

    `configured` is false when EASE_DRIVE_CORS_ORIGINS is unset and the app
    fell back to the local dev default, which is the shape of a deploy that
    will be blocked by CORS. Origins are not secret: the middleware echoes the
    matching one back in a response header on every allowed request.

    `status` deliberately ignores this. A missing allowlist is a
    misconfiguration, not an unhealthy process, and grading it would report
    every local dev run as degraded.
    """

    configured: bool
    origins: list[str]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: DatabaseHealth
    cors: CorsHealth


@router.get("/health", response_model=HealthResponse, summary="Deployment health")
async def read_health(settings: SettingsDep) -> HealthResponse:
    """Report whether the process can actually reach its database."""
    cors = CorsHealth(
        configured=settings.is_cors_configured,
        origins=list(settings.cors_origins),
    )
    if not settings.is_database_configured:
        return HealthResponse(
            status="degraded",
            version=settings.version,
            database=DatabaseHealth(configured=False, error="DATABASE_URL is not set"),
            cors=cors,
        )

    assert settings.database_url is not None  # narrowed by the guard above
    described = describe_database_url(settings.database_url)
    database = DatabaseHealth(
        configured=True,
        host=described["host"],
        database=described["database"],
        pooled=described["pooled"],
    )

    started = time.perf_counter()
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            factory = get_sessionmaker(settings.database_url)
            async with factory() as session:
                await session.execute(text("select 1"))
        database.ok = True
    except TimeoutError:
        database.error = f"timeout after {PROBE_TIMEOUT_SECONDS}s"
    except Exception as error:
        database.error = f"{type(error).__name__}: {error}"
    database.latency_ms = round((time.perf_counter() - started) * 1000, 1)

    return HealthResponse(
        status="ok" if database.ok else "degraded",
        version=settings.version,
        database=database,
        cors=cors,
    )
