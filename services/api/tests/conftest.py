"""Shared fixtures for the API service test suite."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A test client bound to a freshly built app."""
    with TestClient(create_app()) as test_client:
        yield test_client
