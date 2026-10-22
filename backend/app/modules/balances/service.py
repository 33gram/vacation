"""Остаток отпуска: начисление, ручная корректировка и чтение (T-M2-05,
T-M2-08, `FR-BAL-001`, `FR-BAL-002`, `FR-BAL-004`).

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

НАЧИСЛЕНИЕ (T-M2-05). `accrue_leave` пишет РАЗНОСТЬ между тем, сколько
должно быть начислено к дате (`accrual.compute_accrual`), и тем, что уже
начислено за этот рабочий год (сумма событий `accrual`), одним событием.
Разность не больше нуля — событие НЕ пишется (`CHECK` требует `accrual` > 0, а
уменьшение накопленного — это `recalculation`, `T-M2-07`). Поэтому повторный
вызов на ту же дату ничего не пишет. Сумма уже начисленного — это не остаток
(остаток включает списания и корректировки) и ему не конкурент: единственное
место вычисления остатка по-прежнему `get_balance`.

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

from app.models.employee import Employee
from app.models.leave_balance_entry import ACCRUAL, MANUAL_ADJUSTMENT, LeaveBalanceEntry
from app.models.leave_type import LeaveType
from app.modules.balances.accrual import compute_accrual
from app.modules.business_rules.service import (
    ANNUAL_LEAVE_DAYS,
    MONTH_ROUNDING_THRESHOLD_DAYS,
    get_ints,
)

# Начисление работает с ОСНОВНЫМ оплачиваемым отпуском: норма `annual_leave_days`
# (`BR-1`) - его норма. Когда норма станет зависеть от вида (удлинённый и
# дополнительный отпуск - за границами MVP, `A-3.1`), менять надо это место и
# выбор нормы в `accrue_leave`.
MAIN_LEAVE_TYPE_CODE = "annual_paid"

# Типы событий, которые `accrue_leave` считает "уже начисленным". Сегодня -
# один `accrual`. Расширять набор НАДО осознанно, и вот чем это грозит; решение
# по каждому пункту НЕ принято:
#   - `recalculation` (T-M2-07) несёт РАЗНОСТЬ при правке кадровых данных; если
#     его не считать начисленным, следующий `accrue_leave` запишет эту
#     разность второй раз;
#   - правка даты приёма меняет годовщину, значит и `working_year_start`:
#     "уже начислено" нового года равно нулю, и полное начисление пишется
#     заново, а события прежнего года остаются (T-M2-07);
#   - `initial_import` (T-M1-36) несёт начальный остаток за рабочий год; можно
#     ли по такому году ещё и начислять, нигде не сказано.
ALREADY_ACCRUED_ENTRY_TYPES = (ACCRUAL,)


class BalanceEntryError(Exception):
    """Событие нельзя записать: причина, автор, количество или период
    не годятся. Сообщение — для вызывающего (эндпоинт `T-M2-11` переводит
    его в ответ 4xx)."""


async def get_balance(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int,
    working_year_start: datetime.date,
    *,
    as_of: datetime.date | None = None,
) -> Decimal:
    """Остаток сотрудника по виду за рабочий год, начинающийся в
    `working_year_start`, — сумма `quantity` событий (0, если событий нет).

    `as_of` (`BR-8`, остаток на любую дату) — сумма событий с `entry_date` не
    позже этой даты. ГРАНИЦА: функция отражает ЗАПИСАННЫЕ события и не
    придумывает начисление, которого в журнале нет; остаток на дату в будущем
    без начисления сам не растёт. Кому нужна свежая цифра на дату, сначала
    вызывает `accrue_leave` на эту дату."""
    query = select(func.coalesce(func.sum(LeaveBalanceEntry.quantity), 0)).where(
        LeaveBalanceEntry.employee_id == employee_id,
        LeaveBalanceEntry.leave_type_id == leave_type_id,
        LeaveBalanceEntry.working_year_start == working_year_start,
    )
    if as_of is not None:
        query = query.where(LeaveBalanceEntry.entry_date <= as_of)
    result = await session.execute(query)
    return Decimal(result.scalar_one())


async def get_balances_by_working_year(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int,
    *,
    as_of: datetime.date | None = None,
) -> dict[datetime.date, Decimal]:
    """Остаток по каждому рабочему году, в котором есть события (начало года
    -> остаток). Каждое число даёт `get_balance`, своей суммы здесь нет."""
    query = (
        select(LeaveBalanceEntry.working_year_start)
        .where(
            LeaveBalanceEntry.employee_id == employee_id,
            LeaveBalanceEntry.leave_type_id == leave_type_id,
        )
        .distinct()
        .order_by(LeaveBalanceEntry.working_year_start)
    )
    if as_of is not None:
        query = query.where(LeaveBalanceEntry.entry_date <= as_of)
    starts = list((await session.execute(query)).scalars())
    return {
        start: await get_balance(session, employee_id, leave_type_id, start, as_of=as_of)
        for start in starts
    }


async def get_total_balance(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int,
    *,
    as_of: datetime.date | None = None,
) -> Decimal:
    """Остаток за ВСЕ рабочие годы (`BR-8`, ст. 127 ТК РФ: компенсация за все
    неиспользованные отпуска) — сумма остатков по годам, как они даны
    `get_balance`. Год с отрицательным остатком (аванс) уменьшает сумму; если
    нужна иная политика, она принадлежит вызывающему (`T-M1-37`), а не
    этой функции."""
    by_year = await get_balances_by_working_year(session, employee_id, leave_type_id, as_of=as_of)
    return sum(by_year.values(), Decimal(0))


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
    stripped_reason = reason.strip()
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


async def accrue_leave(
    session: AsyncSession,
    *,
    employee_id: int,
    leave_type_id: int,
    as_of: datetime.date,
    excluded_days: int = 0,
    working_year_start: datetime.date | None = None,
) -> Decimal:
    """Начисляет отпуск за рабочий год на дату `as_of` (`FR-BAL-001`):
    пишет ОДНО событие `accrual` с разностью «положено минус уже начислено»
    и возвращает записанное количество; ноль — если писать нечего (событие
    не пишется, исключение не поднимается).

    Работает только с основным видом (`MAIN_LEAVE_TYPE_CODE`): норма
    `annual_leave_days` - норма основного отпуска, и начислять её на другой
    вид нельзя; иной вид - `BalanceEntryError`, событий нет.

    «Уже начислено» - сумма событий типов `ALREADY_ACCRUED_ENTRY_TYPES`
    (сейчас только `accrual`), см. оговорку у константы.

    Норма и порог округления — из `business_rules_config`
    (`annual_leave_days`, `month_rounding_threshold_days`), дата приёма — из
    карточки сотрудника. `excluded_days` и `working_year_start` — как в
    `accrual.compute_accrual` (отпуска без содержания — данные модуля заявок,
    M3; здесь число). Автор события — `None`: начисляет система.

    Транзакцией владеет вызывающий: только `flush`. Строка сотрудника
    блокируется (`FOR UPDATE`) до конца транзакции, чтобы две параллельные
    транзакции не записали одну и ту же разность дважды; это закрыто
    рассуждением, а не проверено тестом на гонку — проверены
    последовательные повторные вызовы."""
    # Вид проверяется ДО блокировки: неверный вызов не должен держать строку
    # сотрудника до конца транзакции вызывающего.
    leave_type_code = await session.scalar(
        select(LeaveType.code).where(LeaveType.id == leave_type_id)
    )
    if leave_type_code is None:
        raise BalanceEntryError("Вид отпуска не найден.")
    if leave_type_code != MAIN_LEAVE_TYPE_CODE:
        raise BalanceEntryError(
            "Начисление выполняется только по основному оплачиваемому отпуску."
        )
    employee = await session.get(Employee, employee_id, with_for_update=True)
    if employee is None:
        raise BalanceEntryError("Сотрудник не найден.")
    rules = await get_ints(session, ANNUAL_LEAVE_DAYS, MONTH_ROUNDING_THRESHOLD_DAYS)
    try:
        accrual = compute_accrual(
            hire_date=employee.hire_date,
            as_of=as_of,
            annual_norm=rules[ANNUAL_LEAVE_DAYS],
            rounding_threshold_days=rules[MONTH_ROUNDING_THRESHOLD_DAYS],
            excluded_days=excluded_days,
            working_year_start=working_year_start,
        )
    except ValueError as error:
        raise BalanceEntryError(str(error)) from error

    already = await session.execute(
        select(func.coalesce(func.sum(LeaveBalanceEntry.quantity), 0)).where(
            LeaveBalanceEntry.employee_id == employee_id,
            LeaveBalanceEntry.leave_type_id == leave_type_id,
            LeaveBalanceEntry.working_year_start == accrual.working_year_start,
            LeaveBalanceEntry.entry_type.in_(ALREADY_ACCRUED_ENTRY_TYPES),
        )
    )
    difference = accrual.accrued - Decimal(already.scalar_one())
    if difference <= 0:
        return Decimal(0)
    session.add(
        LeaveBalanceEntry(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            working_year_start=accrual.working_year_start,
            working_year_end=accrual.working_year_end,
            entry_type=ACCRUAL,
            quantity=difference,
            entry_date=as_of,
            reason=(
                f"Начисление на {as_of.strftime('%d.%m.%Y')}: "
                f"{accrual.counted_months} из 12 месяцев рабочего года"
            ),
            actor_account_id=None,
        )
    )
    await session.flush()
    return difference
