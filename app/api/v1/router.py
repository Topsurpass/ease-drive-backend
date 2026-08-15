"""Aggregates every v1 endpoint router into one mountable router.

``main`` mounts this once under the configured prefix, so adding a resource
means one import and one ``include_router`` call here.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, booking, health, hello

api_router = APIRouter()
api_router.include_router(hello.router)
api_router.include_router(health.router)
api_router.include_router(booking.router)
api_router.include_router(auth.router)
