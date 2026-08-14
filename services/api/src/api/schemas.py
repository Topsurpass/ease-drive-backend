"""Response contracts for the API service.

Anything crossing the HTTP boundary is declared here, so consumers can depend
on a named model instead of an untyped dict.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

HELLO_MESSAGE: Final[str] = "Hello, World!"


class HelloResponse(BaseModel):
    """Body returned by ``GET /``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(
        default=HELLO_MESSAGE,
        min_length=1,
        description="Human-readable greeting.",
        examples=[HELLO_MESSAGE],
    )
