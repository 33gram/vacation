"""Остаток отпуска: ручная корректировка и чтение (T-M2-08, `FR-BAL-002`,
`FR-BAL-004`).

Остаток — СУММА `quantity` событий журнала `leave_balance_entries`
(`plan.md` §4); агрегирующей таблицы нет.

ЕДИНСТВЕННОЕ МЕСТО ВЫЧИСЛЕНИЯ ОСТАТКА — `get_balance` ниже. Начисление
(`T-M2-05`), перерасчёт (`T-M2-07`), эндпоинты (`T-M2-11`), отчёты
(`T-M6-03`) и списание по заявке (`T-M3-34`) получают остаток только
через неё и свои события пишут только через функции этого модуля, а не
собственной суммой по таблице и не прямой вставкой. Это свойство проверяет
`tests/modules/balances/test_balance_journal_guard.py` (на таблицу
ссылается только этот модуль и модель); границы проверки названы там.

ЖУРНАЛ ДОБАВЛЯЕМЫЙ: в модуле нет путей `UPDATE`/`DELETE`; исправление —
новое событие. Запрет на уровне прав БД — `NFR-AUDIT-001` (M6), здесь его
нет.

ТРАНЗАКЦИЯ. Функции записи только `flush`-ят, `commit` делает вызывающий:
начальные остатки при импорте (`T-M1-36`) пишутся вместе с карточкой
сотрудника одной транзакцией, а не по одному коммиту на событие.

ГРАНИЦА `FR-BAL-004` (правило 8). Критерий требует причину И запись в
журнале аудита. Причину закрывает журнал событий (она обязательна на
уровне БД и сервиса); `audit_log` появится в M6 (`T-M6-02`), и вторая
половина критерия этим модулем НЕ закрыта.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leave_balance_entry import MANUAL_ADJUSTMENT, LeaveBalanceEntry


class BalanceEntryError(Exception):
    """Событие нельзя записать: причина, автор, количество или период
    не годятся. Сообщение — для вызывающего (эндпоинт `T-M2-11` переводит
    его в ответ 4xx)."""


async def get_balance(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int,
    working_year_start: datetime.date,
) -> Decimal:
    """Остаток сотрудника по виду за рабочий год, начинающийся в
    `working_year_start`, — сумма `quantity` событий (0, если событий нет)."""
    result = await session.execute(
        select(func.coalesce(func.sum(LeaveBalanceEntry.quantity), 0)).where(
            LeaveBalanceEntry.employee_id == employee_id,
            LeaveBalanceEntry.leave_type_id == leave_type_id,
            LeaveBalanceEntry.working_year_start == working_year_start,
        )
    )
    return Decimal(result.scalar_one())


async def list_entries(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int | None = None,
) -> list[LeaveBalanceEntry]:
    """История событий сотрудника (`FR-BAL-002`): по дате события, при
    равных датах — в порядке записи. У события есть тип, количество со
    знаком, причина и автор (`actor_account_id`; имя автора по аккаунту
    подставляет слой эндпоинта)."""
    query = select(LeaveBalanceEntry).where(LeaveBalanceEntry.employee_id == employee_id)
    if leave_type_id is not None:
        query = query.where(LeaveBalanceEntry.leave_type_id == leave_type_id)
    result = await session.execute(
        query.order_by(LeaveBalanceEntry.entry_date, LeaveBalanceEntry.id)
    )
    return list(result.scalars())


async def adjust_balance(
    session: AsyncSession,
    *,
    employee_id: int,
    leave_type_id: int,
    working_year_start: datetime.date,
    working_year_end: datetime.date,
    quantity: Decimal,
    reason: str,
    actor_account_id: int | None,
    entry_date: datetime.date,
) -> Decimal:
    """Ручная корректировка остатка (`FR-BAL-004`): записывает ОДНО событие
    `manual_adjustment`, прежних не трогает, возвращает новый остаток.

    Причина обязательна, пустая строка и одни пробелы причиной не являются;
    записывается обрезанной. Автор обязателен. `quantity` — со знаком
    (минус уменьшает остаток), ноль — не корректировка."""
    stripped_reason = reason.strip() if isinstance(reason, str) else ""
    if not stripped_reason:
        raise BalanceEntryError("Укажите причину корректировки.")
    if actor_account_id is None:
        raise BalanceEntryError("Не указан автор корректировки.")
    if quantity == 0:
        raise BalanceEntryError("Корректировка на ноль ничего не меняет.")
    if working_year_end <= working_year_start:
        raise BalanceEntryError("Конец рабочего года должен быть позже его начала.")
    session.add(
        LeaveBalanceEntry(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            working_year_start=working_year_start,
            working_year_end=working_year_end,
            entry_type=MANUAL_ADJUSTMENT,
            quantity=quantity,
            entry_date=entry_date,
            reason=stripped_reason,
            actor_account_id=actor_account_id,
        )
    )
    await session.flush()
    return await get_balance(session, employee_id, leave_type_id, working_year_start)
