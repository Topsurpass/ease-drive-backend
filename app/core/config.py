"""Application settings.

Values come from environment variables prefixed with ``EASE_DRIVE_`` or from a
local ``.env`` file, falling back to the defaults declared here.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings.

    Cached so the environment is read once per process. Tests override the
    FastAPI dependency rather than clearing this cache.
    """
    return Settings()
