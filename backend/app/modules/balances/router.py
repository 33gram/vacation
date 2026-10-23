"""Эндпоинты остатка отпуска (T-M2-11): `GET`/`POST /api/employees/{id}/balances*`.

Расчёта остатка в слое API нет: остаток, остаток на дату, сумму по годам, границы
рабочего года и ленивое начисление дают функции модуля `balances`. Здесь только
доступ, сбор ответа и подстановка имени автора по `actor_account_id`.

ДОСТУП (матрица раздела 7 требований; расхождений с правилом карточки нет):
просмотр - `employee_service.can_view_employee` (своя карточка по связи
`accounts.employee_id`, не по роли; руководитель - своя команда по цепочке
подчинения; `hr_admin`/`system_admin`/`accountant` - все); корректировка -
только `hr_admin` (`FR-BAL-004` называет HR-администратора; `accountant` читает, но не
правит; `system_admin` права на корректировку остатка не получает - ВЫБРАНО).

ЧТЕНИЕ ПИШЕТ (`T-M2-19`): `GET` начисляет лениво и коммитит; повтор запроса ничего не
пишет, поэтому безопасен. Начисление идёт не дальше сегодняшней даты: запрос на
будущую дату показывает записанное, прогноз не считается (второй арифметики остатка
быть не должно).
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time_utils import utcnow
from app.db.session import get_db_session
from app.models.account import Account
from app.models.employee import Employee
from app.models.leave_type import LeaveType
from app.modules.auth.rbac import get_current_account, require_roles
from app.modules.balances.accrual import working_year_containing
from app.modules.balances.service import (
    BalanceEntryError,
    accrue_before_read,
    adjust_balance,
    get_balance,
    get_total_balance,
    get_working_year,
    list_entries,
)
from app.modules.employees import employee_service
from app.schemas.balance import (
    BalanceAdjustOut,
    BalanceAdjustRequest,
    BalanceEntryOut,
    EmployeeBalancesOut,
    LeaveTypeBalanceOut,
)
from app.schemas.errors import ErrorDetail

router = APIRouter(prefix="/api/employees/{employee_id}/balances", tags=["balances"])

_require_balance_editor = require_roles("hr_admin")

# Применимый вид (`FR-BAL-002`: «по каждому применимому виду оплачиваемого отпуска») -
# ВЫБРАНО: активный и полностью оплачиваемый (`is_active`, `is_paid = full`); вид без
# событий показывается нулём. Применимость по категориям сотрудников не определяется
# (`A-3.1`). Следствие, которое данные справочника не позволяют отсечь: «командировка»
# (`business_trip`) тоже полностью оплачивается и показывается нулевой строкой.
_APPLICABLE_PAID = "full"


async def _author_names(session: AsyncSession, account_ids: set[int]) -> dict[int, str]:
    if not account_ids:
        return {}
    rows = await session.execute(
        select(Account.id, Employee.full_name, Account.login)
        .outerjoin(Employee, Employee.id == Account.employee_id)
        .where(Account.id.in_(account_ids))
    )
    return {row.id: row.full_name or row.login or f"аккаунт {row.id}" for row in rows}


@router.get("", response_model=EmployeeBalancesOut)
async def get_employee_balances(
    employee_id: int,
    as_of: datetime.date | None = Query(
        default=None, description="Дата расчёта остатка (BR-8); по умолчанию сегодня (UTC)."
    ),
    session: AsyncSession = Depends(get_db_session),
    account: Account = Depends(get_current_account),
) -> EmployeeBalancesOut:
    employee = await employee_service.get_employee(session, employee_id)
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сотрудник не найден")
    if not await employee_service.can_view_employee(session, account, employee_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Недостаточно прав для просмотра остатка сотрудника"
        )
    today = utcnow().date()
    on_date = as_of or today
    # Ленивое начисление перед чтением (T-M2-19); не дальше сегодня. Отказ
    # начисления не перехватывается: устаревшее число молча не отдаётся.
    await accrue_before_read(session, employee_id=employee_id, as_of=min(on_date, today))
    await session.commit()

    leave_types = (
        await session.execute(
            select(LeaveType)
            .where(LeaveType.is_active.is_(True), LeaveType.is_paid == _APPLICABLE_PAID)
            .order_by(LeaveType.id)
        )
    ).scalars().all()

    out: list[LeaveTypeBalanceOut] = []
    histories = {}
    author_ids: set[int] = set()
    for leave_type in leave_types:
        entries = [
            entry
            for entry in await list_entries(session, employee_id, leave_type.id)
            if entry.entry_date <= on_date
        ]
        histories[leave_type.id] = entries
        author_ids |= {e.actor_account_id for e in entries if e.actor_account_id is not None}
    names = await _author_names(session, author_ids)

    for leave_type in leave_types:
        if employee.hire_date <= on_date:
            year_start, year_end = working_year_containing(employee.hire_date, on_date)
            balance = await get_balance(
                session, employee_id, leave_type.id, year_start, as_of=on_date
            )
        else:
            year_start = year_end = None
            balance = 0
        out.append(
            LeaveTypeBalanceOut(
                leave_type_id=leave_type.id,
                code=leave_type.code,
                name=leave_type.name,
                unit=leave_type.unit,
                working_year_start=year_start,
                working_year_end=year_end,
                balance=balance,
                total_balance=await get_total_balance(
                    session, employee_id, leave_type.id, as_of=on_date
                ),
                history=[
                    BalanceEntryOut(
                        id=e.id,
                        entry_type=e.entry_type,
                        quantity=e.quantity,
                        entry_date=e.entry_date,
                        working_year_start=e.working_year_start,
                        working_year_end=e.working_year_end,
                        reason=e.reason,
                        actor_account_id=e.actor_account_id,
                        actor_name=names.get(e.actor_account_id)
                        if e.actor_account_id is not None
                        else None,
                    )
                    for e in histories[leave_type.id]
                ],
            )
        )
    return EmployeeBalancesOut(employee_id=employee_id, as_of=on_date, balances=out)


@router.post(
    "/adjust",
    response_model=BalanceAdjustOut,
    responses={400: {"model": ErrorDetail}, 404: {"model": ErrorDetail}},
)
async def adjust_employee_balance(
    employee_id: int,
    payload: BalanceAdjustRequest,
    session: AsyncSession = Depends(get_db_session),
    account: Account = Depends(_require_balance_editor),
) -> BalanceAdjustOut:
    if await employee_service.get_employee(session, employee_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сотрудник не найден")
    if await session.get(LeaveType, payload.leave_type_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вид отпуска не найден")
    today = utcnow().date()
    entry_date = payload.entry_date or today
    try:
        # Корректировка возвращает новый остаток - это чтение, по которому принимается
        # решение, поэтому начисление идёт и перед ней (T-M2-19).
        await accrue_before_read(session, employee_id=employee_id, as_of=today)
        year_start, year_end = await get_working_year(session, employee_id, entry_date)
        balance = await adjust_balance(
            session,
            employee_id=employee_id,
            leave_type_id=payload.leave_type_id,
            working_year_start=year_start,
            working_year_end=year_end,
            quantity=payload.quantity,
            reason=payload.reason,
            actor_account_id=account.id,
            entry_date=entry_date,
        )
    except BalanceEntryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await session.commit()
    return BalanceAdjustOut(
        leave_type_id=payload.leave_type_id,
        working_year_start=year_start,
        working_year_end=year_end,
        balance=balance,
    )
