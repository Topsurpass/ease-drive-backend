"""Gate tests for GET / — deterministic, offline, sub-second."""

from fastapi import status
from fastapi.testclient import TestClient

from api.schemas import HELLO_MESSAGE, HelloResponse


def test_returns_200(client: TestClient) -> None:
    assert client.get("/").status_code == status.HTTP_200_OK


def test_returns_the_greeting(client: TestClient) -> None:
    assert client.get("/").json() == {"message": HELLO_MESSAGE}


def test_responds_with_json(client: TestClient) -> None:
    content_type = client.get("/").headers["content-type"]
    assert content_type.startswith("application/json")


def test_body_validates_against_the_contract(client: TestClient) -> None:
    """The wire body must round-trip through the declared model."""
    assert HelloResponse.model_validate(client.get("/").json()).message == HELLO_MESSAGE


def test_rejects_unsupported_method(client: TestClient) -> None:
    assert client.post("/").status_code == status.HTTP_405_METHOD_NOT_ALLOWED


def test_unknown_path_is_404(client: TestClient) -> None:
    assert client.get("/nope").status_code == status.HTTP_404_NOT_FOUND
