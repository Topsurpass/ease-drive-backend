"""Hello world endpoint."""

from fastapi import APIRouter, status

from app.api.deps import SettingsDep
from app.schemas.hello import HelloResponse
from app.services.hello import get_greeting

router = APIRouter(tags=["hello"])


@router.get(
    "/hello",
    response_model=HelloResponse,
    status_code=status.HTTP_200_OK,
    summary="Say hello",
)
async def read_hello(settings: SettingsDep) -> HelloResponse:
    """Return the greeting."""
    return HelloResponse(message=get_greeting(settings))
