"""Gate tests for GET /api/v1/hello."""

from fastapi import status
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.schemas.hello import HelloResponse

ENDPOINT = "/api/v1/hello"


def test_returns_200(client: TestClient) -> None:
    assert client.get(ENDPOINT).status_code == status.HTTP_200_OK


def test_returns_the_greeting(client: TestClient) -> None:
    assert client.get(ENDPOINT).json() == {"message": "Hello, World!"}


def test_responds_with_json(client: TestClient) -> None:
    assert client.get(ENDPOINT).headers["content-type"].startswith("application/json")


def test_body_validates_against_the_contract(client: TestClient) -> None:
    """The wire body must round-trip through the declared response model."""
    parsed = HelloResponse.model_validate(client.get(ENDPOINT).json())
    assert parsed.message == "Hello, World!"


def test_rejects_unsupported_method(client: TestClient) -> None:
    assert client.post(ENDPOINT).status_code == status.HTTP_405_METHOD_NOT_ALLOWED


def test_unversioned_path_is_not_served(client: TestClient) -> None:
    """Everything lives under the v1 prefix; the bare path must not resolve."""
    assert client.get("/hello").status_code == status.HTTP_404_NOT_FOUND
    assert client.get("/").status_code == status.HTTP_404_NOT_FOUND


def test_greeting_comes_from_settings() -> None:
    """Changing configuration changes the response, with no code edit."""
    settings = Settings(greeting="Hei, Verden!")
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application) as configured_client:
        body = configured_client.get(f"{settings.api_v1_prefix}/hello").json()
    assert body == {"message": "Hei, Verden!"}
