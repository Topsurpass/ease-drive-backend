"""Gate tests for the application factory and its OpenAPI surface."""

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_factory_returns_independent_apps() -> None:
    """Each call builds a new app, so tests cannot leak state into each other."""
    assert create_app() is not create_app()


def test_applies_settings_to_metadata() -> None:
    application = create_app(Settings(project_name="Custom", version="9.9.9"))
    assert application.title == "Custom"
    assert application.version == "9.9.9"


def test_mounts_every_route_under_the_version_prefix() -> None:
    settings = Settings(api_v1_prefix="/api/v1")
    paths = create_app(settings).openapi()["paths"]
    assert list(paths) == ["/api/v1/hello"]
    assert list(paths["/api/v1/hello"]) == ["get"]


def test_version_prefix_is_configurable() -> None:
    paths = create_app(Settings(api_v1_prefix="/v2")).openapi()["paths"]
    assert list(paths) == ["/v2/hello"]


def test_empty_prefix_serves_at_the_root() -> None:
    """README tells the reader to set the prefix to "" to drop versioning."""
    settings = Settings(api_v1_prefix="")
    with TestClient(create_app(settings)) as unversioned:
        assert unversioned.get("/hello").status_code == 200


def test_openapi_documents_the_response_schema(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    ref = schema["paths"]["/api/v1/hello"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/HelloResponse")
    assert "message" in schema["components"]["schemas"]["HelloResponse"]["properties"]
