"""Gate tests for application settings."""

import pytest

from app.core.config import Settings, get_settings

# Anything that names a specific deployment belongs in that deployment's
# environment, never in the image. This list is what the gate refuses to let
# back into the defaults.
DEPLOYMENT_HOSTS = (
    "vercel.app",
    "fastapicloud.dev",
    "neon.tech",
    "amazonaws.com",
)


def test_declares_sane_defaults() -> None:
    defaults = Settings(_env_file=None)  # type: ignore[call-arg]
    assert defaults.project_name == "Ease Drive API"
    assert defaults.api_v1_prefix == "/api/v1"
    assert defaults.greeting == "Hello, World!"


def test_no_deployment_host_is_hard_coded() -> None:
    """Regression guard for the whole point of this file.

    A hostname compiled into the defaults is permitted on every deploy of
    every environment and cannot be revoked without shipping a release.
    """
    baked = str(Settings(_env_file=None).model_dump())  # type: ignore[call-arg]
    found = [host for host in DEPLOYMENT_HOSTS if host in baked]
    assert not found, f"deployment hosts hard-coded in defaults: {found}"


def test_no_credential_is_hard_coded() -> None:
    """The only secret this app holds is DATABASE_URL, and it has no default."""
    defaults = Settings(_env_file=None)  # type: ignore[call-arg]
    assert defaults.database_url is None
    assert defaults.is_database_configured is False


def test_cors_defaults_to_the_local_dev_loop() -> None:
    """True of any machine running the frontend, so it is not a deployment fact."""
    origins = Settings(_env_file=None).cors_origins  # type: ignore[call-arg]
    assert origins == ("http://localhost:3000", "http://127.0.0.1:3000")


def test_cors_origins_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "EASE_DRIVE_CORS_ORIGINS",
        "https://app.example.com, https://admin.example.com/",
    )
    origins = Settings(_env_file=None).cors_origins  # type: ignore[call-arg]
    assert origins == ("https://app.example.com", "https://admin.example.com")


def test_configured_origins_replace_the_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merging instead would make localhost permanently allowed in production."""
    monkeypatch.setenv("EASE_DRIVE_CORS_ORIGINS", "https://app.example.com")
    assert "http://localhost:3000" not in Settings(_env_file=None).cors_origins  # type: ignore[call-arg]


def test_reports_when_cors_is_unconfigured() -> None:
    """False is what makes a forgotten env var visible at startup and in /health."""
    assert Settings(_env_file=None).is_cors_configured is False  # type: ignore[call-arg]


def test_reports_when_cors_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EASE_DRIVE_CORS_ORIGINS", "https://app.example.com")
    assert Settings(_env_file=None).is_cors_configured is True  # type: ignore[call-arg]


def test_an_explicit_origin_argument_counts_as_configured() -> None:
    """Tests and callers that pass origins in are configuring them too."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        cors_origins=("https://app.example.com",),
    )
    assert settings.is_cors_configured is True


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
