"""`/api/departments*` (T-M1-14, FR-EMP-002).

Просмотр — любой аутентифицированный (дерево подразделений не является
персональными данными и нужно широко, например для выпадающих списков
в карточке сотрудника). Ведение — `hr_admin`/`system_admin` (`plan.md`
§5.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.account import Account
from app.modules.auth.rbac import get_current_account, require_roles
from app.modules.employees import department_service
from app.schemas.department import DepartmentCreate, DepartmentOut, DepartmentUpdate

router = APIRouter(prefix="/api/departments", tags=["departments"])

_require_department_admin = require_roles("hr_admin", "system_admin")


@router.get("", response_model=list[DepartmentOut])
async def get_departments(
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(get_current_account),
) -> list[DepartmentOut]:
    departments = await department_service.list_departments(session)
    return [DepartmentOut.model_validate(d) for d in departments]


@router.post("", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
async def create_department(
    payload: DepartmentCreate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_department_admin),
) -> DepartmentOut:
    try:
        department = await department_service.create_department(session, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return DepartmentOut.model_validate(department)


@router.put("/{department_id}", response_model=DepartmentOut)
async def update_department(
    department_id: int,
    payload: DepartmentUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_department_admin),
) -> DepartmentOut:
    try:
        department = await department_service.update_department(session, department_id, payload)
    except department_service.DepartmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return DepartmentOut.model_validate(department)
