"""Driver registry endpoints.

Reading the list is open to any signed-in staff member, because assigning a
booking needs a driver to pick from. Creating and editing is admin-only: who
counts as vetted is the claim the whole "vetted drivers" promise rests on, and
it should not be something a busy ops account can change while working a queue.

There is no DELETE. Drivers are deactivated, never removed — bookings point at
them, and the record of who drove a trip is exactly what you need after
something goes wrong.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AdminUserDep, CurrentUserDep, SessionDep
from app.domain.vetting import VettingStatus
from app.models.driver import Driver
from app.schemas.driver import (
    CreateDriverRequest,
    DriverError,
    DriverPage,
    DriverResponse,
    UpdateDriverRequest,
)
from app.services import driver as driver_service

router = APIRouter(prefix="/admin/drivers", tags=["ops console"])

_Responses = dict[int | str, dict[str, Any]]
_NOT_FOUND: _Responses = {status.HTTP_404_NOT_FOUND: {"model": DriverError}}
_FORBIDDEN: _Responses = {status.HTTP_403_FORBIDDEN: {"model": DriverError}}


def _response(driver: Driver) -> DriverResponse:
    return DriverResponse.of(
        driver, assignable=driver_service.can_take_bookings(driver)
    )


@router.get("", response_model=DriverPage, summary="List drivers")
async def list_drivers(
    session: SessionDep,
    _: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=driver_service.MAX_PAGE_SIZE)] = (
        driver_service.DEFAULT_PAGE_SIZE
    ),
    vetting_status: Annotated[list[VettingStatus] | None, Query()] = None,
    active: Annotated[bool | None, Query()] = None,
    assignable_only: Annotated[bool, Query()] = False,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> DriverPage:
    """A page of drivers, alphabetical.

    `assignable_only=true` is what the assign-driver picker calls: it returns
    exactly the drivers the assign endpoint will accept, so the picker cannot
    offer someone who is then rejected.
    """
    drivers, total = await driver_service.list_drivers(
        session,
        filters=driver_service.DriverFilters(
            vetting_statuses=tuple(vetting_status or ()),
            active=active,
            assignable_only=assignable_only,
            query=q,
        ),
        page=page,
        limit=limit,
    )
    return DriverPage(
        items=[_response(driver) for driver in drivers],
        total=total,
        page=max(1, page),
        limit=limit,
    )


@router.post(
    "",
    response_model=DriverResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a driver",
    responses=_FORBIDDEN,
)
async def create_driver(
    request: CreateDriverRequest, session: SessionDep, _: AdminUserDep
) -> DriverResponse:
    """Register a driver. Admin only."""
    driver = await driver_service.create_driver(
        session,
        full_name=request.full_name,
        phone=request.phone,
        email=str(request.email) if request.email else None,
        vetting_status=request.vetting_status.value,
        vehicle_make=request.vehicle_make,
        vehicle_model=request.vehicle_model,
        vehicle_plate=request.vehicle_plate,
    )
    return _response(driver)


@router.get(
    "/{driver_id}",
    response_model=DriverResponse,
    summary="One driver",
    responses=_NOT_FOUND,
)
async def get_driver(
    driver_id: uuid.UUID, session: SessionDep, _: CurrentUserDep
) -> DriverResponse:
    try:
        driver = await driver_service.get_driver(session, driver_id)
    except driver_service.DriverNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DriverError(
                code="not_found", message="No driver with that id."
            ).model_dump(by_alias=True, mode="json"),
        ) from error
    return _response(driver)


@router.patch(
    "/{driver_id}",
    response_model=DriverResponse,
    summary="Update a driver",
    responses={**_NOT_FOUND, **_FORBIDDEN},
)
async def update_driver(
    driver_id: uuid.UUID,
    request: UpdateDriverRequest,
    session: SessionDep,
    _: AdminUserDep,
) -> DriverResponse:
    """Partial update. Admin only.

    `exclude_unset` is what makes this a real PATCH: a body carrying only
    `isActive` leaves the phone number alone instead of nulling it.
    """
    try:
        driver = await driver_service.get_driver(session, driver_id)
    except driver_service.DriverNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DriverError(
                code="not_found", message="No driver with that id."
            ).model_dump(by_alias=True, mode="json"),
        ) from error

    changes = request.model_dump(exclude_unset=True)
    if "vetting_status" in changes and changes["vetting_status"] is not None:
        changes["vetting_status"] = VettingStatus(changes["vetting_status"]).value
    if "email" in changes and changes["email"] is not None:
        changes["email"] = str(changes["email"])

    driver = await driver_service.update_driver(session, driver, **changes)
    return _response(driver)
