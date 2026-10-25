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
пишет, поэтому безопасен. Начисление всегда идёт до сегодняшней даты (и не дальше даты
увольнения), а `as_of` - только фильтр показа: смотреть историю на дату - не повод менять
журнал задним числом; запрос на будущую дату показывает записанное, прогноз не считается
(второй арифметики остатка быть не должно).

ПОРЯДОК 404/403: в `GET` сначала 404, потом 403 (как у `GET /api/employees/{id}`); в
`POST` роль проверяет зависимость FastAPI ДО тела обработчика, поэтому там 403 раньше
404. Выравнивать значило бы тащить проверку роли в тело - не делаем.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time_utils import utcnow
from app.db.session import get_db_session
from app.models.account import Account
from app.models.leave_type import LeaveType
from app.modules.auth.rbac import get_current_account, require_roles
from app.modules.auth.repository import SYSTEM_AUTHOR_LABEL, get_author_display_names
from app.modules.balances.accrual import working_year_containing
from app.modules.balances.service import (
    BalanceEntryError,
    accrue_before_read,
    adjust_balance,
    get_balance,
    get_total_balance,
    get_working_year,
    list_entries,
    list_leave_type_ids_with_events,
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

@router.get("", response_model=EmployeeBalancesOut)
async def get_employee_balances(
    employee_id: int,
    as_of: datetime.date | None = Query(
        default=None,
        description=(
            "Дата показа остатка (BR-8); по умолчанию сегодня (UTC). Это только ФИЛЬТР "
            "показа: журнал по ней не меняется, начисление идёт до сегодняшнего дня."
        ),
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
    # Ленивое начисление перед чтением (T-M2-19) ВСЕГДА до сегодняшнего дня (и не дальше
    # даты увольнения - это решает модуль): запрос на прошлую дату журнал задним числом
    # не меняет, запрос на будущую - не пишет вперёд и показывает записанное, прогноз не
    # считается. Отказ начисления не перехватывается: устаревшее число молча не отдаётся.
    await accrue_before_read(session, employee_id=employee_id, as_of=today)
    await session.commit()

    # Набор видов (FR-BAL-002: «по каждому применимому виду оплачиваемого отпуска») -
    # ВЫБРАНО (требования v1.26, раздел 13 п. 32): виды с признаком «по виду ведётся
    # остаток» (`balance_tracked`) и активные, ПЛЮС любой вид, по которому у сотрудника
    # уже есть события журнала: деактивация вида или снятие с него признака не должны
    # прятать записанную историю. Нулевая строка у вида с признаком осмысленна («у вас
    # пока ноль», в том числе накопленной компенсации по отгулу) - в отличие от прежней
    # «Командировка: 0»; вид без признака нулём не показывается. Начисления по отгулу
    # эта выборка не заводит (`FR-DAYOFF-001`, M3).
    with_events = await list_leave_type_ids_with_events(session, employee_id)
    leave_types = (
        (
            await session.execute(
                select(LeaveType)
                .where(
                    or_(
                        and_(LeaveType.balance_tracked.is_(True), LeaveType.is_active.is_(True)),
                        LeaveType.id.in_(with_events),
                    )
                )
                .order_by(LeaveType.id)
            )
        )
        .scalars()
        .all()
    )

    # Рабочий год даты от вида отпуска не зависит. Чистая функция, а не
    # `get_working_year`: сотрудник уже загружен, лишний запрос не нужен (в `POST` берётся
    # `get_working_year`, она оборачивает эту же функцию).
    if employee.hire_date <= on_date:
        year_start, year_end = working_year_containing(employee.hire_date, on_date)
    else:
        year_start = year_end = None

    histories = {
        leave_type.id: await list_entries(session, employee_id, leave_type.id, as_of=on_date)
        for leave_type in leave_types
    }
    author_ids = {
        e.actor_account_id
        for entries in histories.values()
        for e in entries
        if e.actor_account_id is not None
    }
    names = await get_author_display_names(session, author_ids)

    out: list[LeaveTypeBalanceOut] = []
    for leave_type in leave_types:
        balance = (
            await get_balance(session, employee_id, leave_type.id, year_start, as_of=on_date)
            if year_start is not None
            else Decimal(0)
        )
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
                        # Только подпись: идентификатор аккаунта наружу не отдаётся.
                        actor_name=SYSTEM_AUTHOR_LABEL
                        if e.actor_account_id is None
                        else names.get(e.actor_account_id, SYSTEM_AUTHOR_LABEL),
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
