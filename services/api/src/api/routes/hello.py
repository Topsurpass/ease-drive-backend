"""The hello world route."""

from fastapi import APIRouter, status

from api.schemas import HelloResponse

router = APIRouter(tags=["hello"])


@router.get(
    "/",
    response_model=HelloResponse,
    status_code=status.HTTP_200_OK,
    summary="Say hello",
)
async def read_hello() -> HelloResponse:
    """Return the greeting."""
    return HelloResponse()
