"""`/api/leave-types*` (T-M2-10, FR-ADMIN-001).

Чтение - любой аутентифицированный аккаунт (справочник нужен формам заявок и «Моему
отпуску»). Ведение - `hr_admin` и `system_admin`: строка матрицы раздела 7 «Ведение
справочников (виды отпусков, подразделения, календарь)» даёт его HR и Админу.
`DELETE` нет сознательно: вид выводят признаком `is_active`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.account import Account
from app.modules.auth.rbac import get_current_account, require_roles
from app.modules.leave_types import service
from app.schemas.leave_type import LeaveTypeCreate, LeaveTypeOut, LeaveTypeUpdate

router = APIRouter(prefix="/api/leave-types", tags=["leave-types"])

_require_directory_admin = require_roles("hr_admin", "system_admin")


@router.get("", response_model=list[LeaveTypeOut])
async def get_leave_types(
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(get_current_account),
) -> list[LeaveTypeOut]:
    return [LeaveTypeOut.model_validate(t) for t in await service.list_leave_types(session)]


@router.post("", response_model=LeaveTypeOut, status_code=status.HTTP_201_CREATED)
async def create_leave_type(
    payload: LeaveTypeCreate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_directory_admin),
) -> LeaveTypeOut:
    try:
        leave_type = await service.create_leave_type(session, payload)
    except service.LeaveTypeCodeConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return LeaveTypeOut.model_validate(leave_type)


@router.put("/{leave_type_id}", response_model=LeaveTypeOut)
async def update_leave_type(
    leave_type_id: int,
    payload: LeaveTypeUpdate,
    session: AsyncSession = Depends(get_db_session),
    _: Account = Depends(_require_directory_admin),
) -> LeaveTypeOut:
    try:
        leave_type = await service.update_leave_type(session, leave_type_id, payload)
    except service.LeaveTypeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except service.LeaveTypeCodeImmutableError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except service.LeaveTypeHasEventsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return LeaveTypeOut.model_validate(leave_type)
