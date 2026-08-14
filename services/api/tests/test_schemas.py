"""Gate tests for the response contract."""

import pytest
from pydantic import ValidationError

from api.schemas import HELLO_MESSAGE, HelloResponse


def test_defaults_to_the_greeting() -> None:
    assert HelloResponse().message == HELLO_MESSAGE


def test_is_immutable() -> None:
    """Frozen, so a handler cannot mutate a shared response by accident."""
    with pytest.raises(ValidationError):
        HelloResponse().message = "tampered"


def test_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        HelloResponse.model_validate({"message": HELLO_MESSAGE, "sneaky": 1})


def test_rejects_empty_message() -> None:
    with pytest.raises(ValidationError):
        HelloResponse(message="")


def test_rejects_non_string_message() -> None:
    with pytest.raises(ValidationError):
        HelloResponse.model_validate({"message": 42})
