"""`/api/admin/production-calendar/{year}` (T-M1-17, FR-ADMIN-003) —
доступ только `system_admin`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.account import Account
from app.modules.admin import production_calendar_service
from app.modules.auth.rbac import require_roles
from app.schemas.errors import AUTH_RESPONSES, error_responses
from app.schemas.production_calendar import (
    ProductionCalendarDayOut,
    ProductionCalendarYearIn,
    ProductionCalendarYearOut,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], responses=AUTH_RESPONSES)

_require_system_admin = require_roles("system_admin")


@router.get("/production-calendar/{year}", response_model=ProductionCalendarYearOut)
async def get_production_calendar(
    year: int,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_system_admin),
) -> ProductionCalendarYearOut:
    days = await production_calendar_service.get_year(session, year)
    return ProductionCalendarYearOut(
        year=year, days=[ProductionCalendarDayOut.model_validate(d) for d in days]
    )


@router.put(
    "/production-calendar/{year}",
    response_model=ProductionCalendarYearOut,
    responses=error_responses(400),
)
async def put_production_calendar(
    year: int,
    payload: ProductionCalendarYearIn,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_system_admin),
) -> ProductionCalendarYearOut:
    try:
        days = await production_calendar_service.replace_year(session, year, payload.days)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ProductionCalendarYearOut(
        year=year, days=[ProductionCalendarDayOut.model_validate(d) for d in days]
    )
