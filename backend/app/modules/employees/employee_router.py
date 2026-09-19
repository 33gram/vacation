"""`/api/employees*` (T-M1-15, FR-EMP-001 в редакции v1.7).

Ведение (`POST`/`PUT`) — `hr_admin`/`system_admin`. Просмотр — любой
аутентифицированный, но что именно видно, решает
`employee_service.can_view_employee`/`list_visible_employees`, а не
`require_roles` (своя карточка, своя команда, либо привилегированная
роль — `plan.md` §5.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.account import Account
from app.modules.auth.password_policy import WeakPasswordError
from app.modules.auth.rbac import get_current_account, require_roles
from app.modules.employees import employee_service
from app.schemas.employee import EmployeeCreate, EmployeeDeactivate, EmployeeOut, EmployeeUpdate

router = APIRouter(prefix="/api/employees", tags=["employees"])

_require_employee_admin = require_roles("hr_admin", "system_admin")


@router.get("", response_model=list[EmployeeOut])
async def get_employees(
    include_terminated: bool = False,
    session: AsyncSession = Depends(get_db_session),
    account: Account = Depends(get_current_account),
) -> list[EmployeeOut]:
    employees = await employee_service.list_visible_employees(
        session, account, include_terminated=include_terminated
    )
    return [EmployeeOut.model_validate(e) for e in employees]


@router.post("", response_model=EmployeeOut, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeCreate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_employee_admin),
) -> EmployeeOut:
    try:
        employee = await employee_service.create_employee(session, payload)
    except WeakPasswordError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return EmployeeOut.model_validate(employee)


@router.get("/{employee_id}", response_model=EmployeeOut)
async def get_employee(
    employee_id: int,
    session: AsyncSession = Depends(get_db_session),
    account: Account = Depends(get_current_account),
) -> EmployeeOut:
    employee = await employee_service.get_employee(session, employee_id)
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    if not await employee_service.can_view_employee(session, account, employee_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    return EmployeeOut.model_validate(employee)


@router.put("/{employee_id}", response_model=EmployeeOut)
async def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_employee_admin),
) -> EmployeeOut:
    try:
        employee = await employee_service.update_employee(session, employee_id, payload)
    except employee_service.EmployeeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return EmployeeOut.model_validate(employee)


@router.post("/{employee_id}/deactivate", response_model=EmployeeOut)
async def deactivate_employee(
    employee_id: int,
    payload: EmployeeDeactivate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_employee_admin),
) -> EmployeeOut:
    try:
        employee = await employee_service.deactivate_employee(session, employee_id, payload)
    except employee_service.EmployeeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return EmployeeOut.model_validate(employee)
