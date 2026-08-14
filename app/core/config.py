"""Application settings.

Values come from environment variables prefixed with ``EASE_DRIVE_`` or from a
local ``.env`` file, falling back to the defaults declared here.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.cors import parse_origins


class Settings(BaseSettings):
    """Runtime configuration for the API."""

    model_config = SettingsConfigDict(
        env_prefix="EASE_DRIVE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    project_name: str = "Ease Drive API"
    version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    greeting: str = "Hello, World!"

    # Neon Postgres connection string. Optional so the app still boots and can
    # report the problem: routes that need a database return 503 instead of the
    # process failing to import. Same posture as nibbs-report's getSql().
    #
    # Read from unprefixed DATABASE_URL, not EASE_DRIVE_DATABASE_URL. Neon,
    # Vercel, Render and Railway all inject that exact name, so requiring the
    # prefix would mean hand-copying the credential on every host. The prefixed
    # form still works as a fallback for anyone who sets it that way.
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "EASE_DRIVE_DATABASE_URL"),
    )

    # Browser origins allowed to call this API.
    #
    # This must list the origin the BROWSER is on, which is the frontend, not
    # this backend's own URL. A request the page makes to its own origin is not
    # cross-origin and never consults this list.
    #
    # NoDecode plus the validator below means a hosting dashboard can hold a
    # plain comma-separated line instead of JSON, which is what those
    # single-line fields invite. Entries are normalized, so a pasted trailing
    # slash does not silently stop matching.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = (
        # The deployed frontend. This is the one that matters: it is the origin
        # the browser is on when the booking form posts.
        "https://ease-drive-frontend.vercel.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        # The deployed backend's own origin. Same-origin calls (the /docs page
        # trying it out) never need this, but it costs nothing and makes the
        # deployed host explicit.
        "https://ease-drive-backend.fastapicloud.dev",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> tuple[str, ...]:
        return parse_origins(value)

    @property
    def is_database_configured(self) -> bool:
        """True when a database URL is present and non-blank."""
        return bool(self.database_url and self.database_url.strip())


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings.

    Cached so the environment is read once per process. Tests override the
    FastAPI dependency rather than clearing this cache.
    """
    return Settings()
