"""Wire contract for the driver registry.

Driver contact details are not masked the way customer ones are: a driver is a
counterparty the business works with, staff need to ring them to arrange a
trip, and the whole point of the screen is to manage them. The protection here
is that the endpoints are admin-only.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic.alias_generators import to_camel

from app.domain.vetting import VettingStatus
from app.models.driver import Driver
from app.schemas.booking import MIN_PHONE_DIGITS, PHONE_PATTERN

MAX_NAME = 80
MAX_VEHICLE_FIELD = 60
MAX_PLATE = 16


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, str_strip_whitespace=True
    )


class DriverResponse(CamelModel):
    """A driver as the console renders them."""

    id: str
    full_name: str
    phone: str
    email: EmailStr | None = None
    vetting_status: VettingStatus
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    vehicle_plate: str | None = None
    is_active: bool
    #: Derived, not stored: whether this driver can be assigned right now.
    #: Computed here so the console never has to re-implement the rule and
    #: get it subtly wrong.
    is_assignable: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, driver: Driver, *, assignable: bool) -> "DriverResponse":
        return cls(
            id=str(driver.id),
            full_name=driver.full_name,
            phone=driver.phone,
            email=driver.email,
            vetting_status=VettingStatus(driver.vetting_status),
            vehicle_make=driver.vehicle_make,
            vehicle_model=driver.vehicle_model,
            vehicle_plate=driver.vehicle_plate,
            is_active=driver.is_active,
            is_assignable=assignable,
            created_at=driver.created_at,
            updated_at=driver.updated_at,
        )


class DriverPage(CamelModel):
    """A page of drivers."""

    items: list[DriverResponse]
    total: int
    page: int
    limit: int


class _PhoneMixin(BaseModel):
    """Shared phone rule, identical to the booking form's.

    A driver's number goes through the same validation as a customer's — same
    pattern, same digit floor — so the two cannot drift into accepting
    different things and confusing whoever has to dial one.
    """

    @field_validator("phone", check_fields=False)
    @classmethod
    def _has_enough_digits(cls, value: str) -> str:
        if sum(character.isdigit() for character in value) < MIN_PHONE_DIGITS:
            raise ValueError("Phone number looks too short.")
        return value


class CreateDriverRequest(CamelModel, _PhoneMixin):
    """Add a driver to the registry."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    full_name: str = Field(min_length=2, max_length=MAX_NAME)
    phone: str = Field(pattern=PHONE_PATTERN)
    email: EmailStr | None = None
    #: New drivers start unvetted. Marking one verified is a separate,
    #: deliberate act, not something you can do by accident while typing in
    #: their phone number.
    vetting_status: VettingStatus = VettingStatus.PENDING
    vehicle_make: str | None = Field(default=None, max_length=MAX_VEHICLE_FIELD)
    vehicle_model: str | None = Field(default=None, max_length=MAX_VEHICLE_FIELD)
    vehicle_plate: str | None = Field(default=None, max_length=MAX_PLATE)


class UpdateDriverRequest(CamelModel, _PhoneMixin):
    """Partial update. Omitted fields are left alone."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    full_name: str | None = Field(default=None, min_length=2, max_length=MAX_NAME)
    phone: str | None = Field(default=None, pattern=PHONE_PATTERN)
    email: EmailStr | None = None
    vetting_status: VettingStatus | None = None
    vehicle_make: str | None = Field(default=None, max_length=MAX_VEHICLE_FIELD)
    vehicle_model: str | None = Field(default=None, max_length=MAX_VEHICLE_FIELD)
    vehicle_plate: str | None = Field(default=None, max_length=MAX_PLATE)
    is_active: bool | None = None


class DriverError(CamelModel):
    """Failure body for the driver endpoints."""

    ok: Literal[False] = False
    code: Literal["not_found", "validation_error", "forbidden", "unavailable"]
    message: str
