"""Application settings.

Values come from environment variables prefixed with ``EASE_DRIVE_`` or from a
local ``.env`` file, falling back to the defaults declared here.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.cors import parse_origins

#: RFC 7518 §3.2: an HMAC key should be at least as long as the digest it
#: feeds. SHA-256 means 32 bytes.
MIN_JWT_SECRET_BYTES = 32


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
    # Deployment hostnames are deliberately absent from this default. Every
    # environment names its own, through EASE_DRIVE_CORS_ORIGINS:
    #
    #     EASE_DRIVE_CORS_ORIGINS=https://your-frontend.vercel.app
    #
    # The value REPLACES this default rather than extending it, because an
    # allowlist you cannot shrink is not an allowlist: a hostname baked into
    # the code stays permitted on every deploy, and nobody can revoke it
    # without shipping a release.
    #
    # This must list the origin the BROWSER is on, which is the frontend, not
    # this backend's own URL. A request the page makes to its own origin is not
    # cross-origin and never consults this list.
    #
    # The default is the local dev loop, which is not a deployment fact and is
    # true of any machine running the Next.js dev server. It exists so
    # `fastapi dev` works against a fresh clone with no configuration.
    #
    # NoDecode plus the validator below means a hosting dashboard can hold a
    # plain comma-separated line instead of JSON, which is what those
    # single-line fields invite. Entries are normalized, so a pasted trailing
    # slash does not silently stop matching.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )

    # Signing key for access tokens, and the key the refresh chain is derived
    # from. Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
    #
    # Optional for the same reason as `database_url`: the app must still boot
    # and be able to say what is wrong. Auth routes return 503 when it is
    # missing rather than the process failing to import, and startup logs a
    # warning. There is deliberately no default value — a fallback secret is a
    # published secret, and it would be the one every unconfigured deploy uses.
    #
    # Rotating this invalidates every live session: access tokens fail to
    # verify and refresh chains stop deriving. That is the intended behaviour
    # for a compromised key, but it is not a routine operation.
    jwt_secret: str | None = None

    #: Access tokens are short-lived because they cannot be revoked. Fifteen
    #: minutes bounds the damage from a leaked one without making refresh
    #: traffic constant.
    access_token_ttl_seconds: int = 15 * 60

    #: Refresh tokens are revocable, so they can be long-lived. Two weeks means
    #: staff are not re-typing a password every day.
    refresh_token_ttl_seconds: int = 14 * 24 * 60 * 60

    #: How long after a refresh token is rotated its predecessor may still be
    #: presented and get the same successor back, instead of being treated as
    #: theft. Covers honest races — two tabs, a retried request, a second
    #: serverless instance — which are measured in milliseconds, not seconds.
    #: See `app.services.auth` for why this is what makes rotation safe.
    refresh_grace_seconds: int = 30

    #: Failed sign-ins before an account is locked, and for how long.
    max_failed_logins: int = 5
    lockout_seconds: int = 15 * 60

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> tuple[str, ...]:
        return parse_origins(value)

    @field_validator("jwt_secret")
    @classmethod
    def _secret_is_long_enough(cls, value: str | None) -> str | None:
        """Refuse a signing key shorter than the hash it feeds.

        RFC 7518 §3.2 requires an HMAC key at least as long as the digest —
        32 bytes for SHA-256 — and PyJWT warns when it is not. A warning in a
        log nobody reads is not a control, so this is a hard failure at boot.
        Blank is treated as absent, which `is_auth_configured` then reports.
        """
        if value is None or not value.strip():
            return None

        secret = value.strip()
        if len(secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"EASE_DRIVE_JWT_SECRET must be at least {MIN_JWT_SECRET_BYTES} "
                "bytes. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return secret

    @property
    def is_database_configured(self) -> bool:
        """True when a database URL is present and non-blank."""
        return bool(self.database_url and self.database_url.strip())

    @property
    def is_auth_configured(self) -> bool:
        """True when a signing key is present and non-blank."""
        return bool(self.jwt_secret and self.jwt_secret.strip())

    @property
    def is_cors_configured(self) -> bool:
        """True when the allowlist came from configuration, not the default.

        A deploy that forgets EASE_DRIVE_CORS_ORIGINS is left allowing only
        localhost, and the browser reports that as a CORS failure with nothing
        in the server log. This is what lets startup and /health say so out
        loud instead.
        """
        return "cors_origins" in self.model_fields_set


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings.

    Cached so the environment is read once per process. Tests override the
    FastAPI dependency rather than clearing this cache.
    """
    return Settings()
