"""Business logic for the hello resource."""

from app.core.config import Settings


def get_greeting(settings: Settings) -> str:
    """Return the configured greeting text."""
    return settings.greeting
