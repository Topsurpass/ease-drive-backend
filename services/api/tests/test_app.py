"""Gate tests for the application factory and its OpenAPI surface."""

from fastapi.testclient import TestClient

from api.main import create_app


def test_factory_returns_independent_apps() -> None:
    """Each call builds a new app, so tests cannot leak state into each other."""
    assert create_app() is not create_app()


def test_exposes_exactly_one_documented_route() -> None:
    paths = create_app().openapi()["paths"]
    assert list(paths) == ["/"]
    assert list(paths["/"]) == ["get"]


def test_openapi_documents_the_response_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    ref = schema["paths"]["/"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/HelloResponse")
    assert "message" in schema["components"]["schemas"]["HelloResponse"]["properties"]
