"""Schemas for the hello resource."""

from pydantic import BaseModel, ConfigDict, Field


class HelloResponse(BaseModel):
    """Body returned by ``GET /api/v1/hello``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(
        min_length=1,
        description="Human-readable greeting.",
        examples=["Hello, World!"],
    )
