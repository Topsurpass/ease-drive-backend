"""Gate tests for the hello response contract."""

import pytest
from pydantic import ValidationError

from app.schemas.hello import HelloResponse


def test_accepts_a_message() -> None:
    assert HelloResponse(message="Hello, World!").message == "Hello, World!"


def test_requires_a_message() -> None:
    with pytest.raises(ValidationError):
        HelloResponse.model_validate({})


def test_is_immutable() -> None:
    """Frozen, so a handler cannot mutate a shared response by accident."""
    response = HelloResponse(message="Hello, World!")
    with pytest.raises(ValidationError):
        response.message = "tampered"


def test_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        HelloResponse.model_validate({"message": "hi", "sneaky": 1})


def test_rejects_empty_message() -> None:
    with pytest.raises(ValidationError):
        HelloResponse(message="")


def test_rejects_non_string_message() -> None:
    with pytest.raises(ValidationError):
        HelloResponse.model_validate({"message": 42})
