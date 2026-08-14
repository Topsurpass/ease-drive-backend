"""Gate tests for application settings."""

import pytest

from app.core.config import Settings, get_settings


def test_declares_sane_defaults() -> None:
    defaults = Settings(_env_file=None)  # type: ignore[call-arg]
    assert defaults.project_name == "Ease Drive API"
    assert defaults.api_v1_prefix == "/api/v1"
    assert defaults.greeting == "Hello, World!"


def test_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EASE_DRIVE_GREETING", "Hola")
    assert Settings(_env_file=None).greeting == "Hola"  # type: ignore[call-arg]


def test_ignores_unprefixed_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only EASE_DRIVE_* is ours; a bare GREETING must not bleed in."""
    monkeypatch.setenv("GREETING", "wrong")
    assert Settings(_env_file=None).greeting == "Hello, World!"  # type: ignore[call-arg]


def test_is_immutable() -> None:
    with pytest.raises(ValueError, match="frozen"):
        Settings().greeting = "tampered"


def test_get_settings_is_cached() -> None:
    """One environment read per process, so config cannot drift mid-request."""
    get_settings.cache_clear()
    assert get_settings() is get_settings()
