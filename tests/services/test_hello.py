"""Gate tests for the hello service.

The service is pure and HTTP-free, so it is tested without a client.
"""

from app.core.config import Settings
from app.services.hello import get_greeting


def test_returns_the_configured_greeting() -> None:
    assert get_greeting(Settings(greeting="Bonjour")) == "Bonjour"


def test_defaults_to_hello_world(settings: Settings) -> None:
    assert get_greeting(settings) == "Hello, World!"
