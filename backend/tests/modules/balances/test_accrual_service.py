"""Запись начисления в журнал, остаток на дату и сумма по годам
(T-M2-05, `FR-BAL-001`, `BR-8`).

Арифметика — `test_accrual_arithmetic.py`; здесь — что делает сервис с
журналом. Ожидаемые числа посчитаны на бумаге: приём 10.03.2026, норма 28.
`FR-BAL-002`/`FR-BAL-004` покрыты в `test_service.py` и не повторяются.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.employee import Employee
from app.models.leave_balance_entry import ACCRUAL, LeaveBalanceEntry
from app.modules.balances.service import (
    ALREADY_ACCRUED_ENTRY_TYPES,
    BalanceEntryError,
    accrue_leave,
    adjust_balance,
    get_balance,
    get_balances_by_working_year,
    get_total_balance,
)
from tests.modules.balances.helpers import Context, make_context

D = datetime.date
HIRE = D(2026, 3, 10)
YEAR_START = D(2026, 3, 10)


async def _hired(db_session, suffix: str = "a") -> Context:
    ctx = await make_context(db_session, suffix)
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = HIRE
    await db_session.flush()
    return ctx


async def _accrue(db_session, ctx: Context, as_of: datetime.date, **kw) -> Decimal:
    return await accrue_leave(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=as_of,
        **kw,
    )


async def _events(db_session, ctx: Context) -> list[LeaveBalanceEntry]:
    result = await db_session.execute(
        select(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == ctx.employee_id)
        .order_by(LeaveBalanceEntry.id)
    )
    return list(result.scalars())


async def test_fr_bal_001_accrual_writes_one_positive_system_event_for_the_working_year(
    db_session,
) -> None:
    ctx = await _hired(db_session)

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    (event,) = await _events(db_session, ctx)
    assert written == Decimal("18.67")
    assert (event.entry_type, event.quantity, event.actor_account_id) == (
        ACCRUAL,
        Decimal("18.67"),
        None,
    )
    assert (event.working_year_start, event.working_year_end) == (YEAR_START, D(2027, 3, 10))
    assert event.entry_date == D(2026, 10, 24)
    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == (
        Decimal("18.67")
    )


async def test_fr_bal_001_a_repeated_call_for_the_same_date_writes_nothing(db_session) -> None:
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    assert written == 0
    assert len(await _events(db_session, ctx)) == 1


async def test_fr_bal_001_a_later_date_writes_only_the_difference(db_session) -> None:
    # 10.12: 9 полных месяцев, 28 * 9 / 12 = 21,00; уже начислено 18,67.
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))

    written = await _accrue(db_session, ctx, D(2026, 12, 10))

    events = await _events(db_session, ctx)
    assert written == Decimal("2.33")
    assert [event.quantity for event in events] == [Decimal("18.67"), Decimal("2.33")]
    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == (
        Decimal("21.00")
    )


async def test_fr_bal_001_a_zero_or_negative_difference_writes_no_event_and_does_not_fail(
    db_session,
) -> None:
    # Уже начислено 21,00 (на 10.12); расчёт на более раннюю дату даёт 18,67 -
    # разность отрицательна. accrual строго положителен по CHECK, а
    # уменьшение накопленного - recalculation (T-M2-07), не начисление.
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 12, 10))

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    assert written == 0
    assert len(await _events(db_session, ctx)) == 1
    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == (
        Decimal("21.00")
    )


async def test_fr_bal_001_nothing_to_accrue_on_the_hire_date(db_session) -> None:
    ctx = await _hired(db_session)

    assert await _accrue(db_session, ctx, HIRE) == 0
    assert await _events(db_session, ctx) == []


async def test_fr_bal_001_the_norm_comes_from_business_rules_config_not_from_code(
    db_session,
) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = '30' WHERE key = 'annual_leave_days'")
    )
    ctx = await _hired(db_session)

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    assert written == Decimal("20.00")  # 30 * 8 / 12


async def test_fr_bal_001_the_rounding_threshold_comes_from_business_rules_config(
    db_session,
) -> None:
    await db_session.execute(
        text(
            "UPDATE business_rules_config SET value = '14' "
            "WHERE key = 'month_rounding_threshold_days'"
        )
    )
    ctx = await _hired(db_session)

    written = await _accrue(db_session, ctx, D(2026, 10, 23))  # остаток 14 дней

    assert written == Decimal("18.67")  # 8 месяцев


async def test_fr_bal_001_excluded_days_shift_the_recorded_working_year_end(db_session) -> None:
    ctx = await _hired(db_session)

    await _accrue(db_session, ctx, D(2027, 3, 25), excluded_days=16, working_year_start=HIRE)

    (event,) = await _events(db_session, ctx)
    assert event.working_year_end == D(2027, 3, 26)
    assert event.quantity == Decimal("28.00")


async def test_fr_bal_001_an_unknown_employee_is_refused_and_nothing_is_written(
    db_session,
) -> None:
    ctx = await _hired(db_session)

    with pytest.raises(BalanceEntryError):
        await accrue_leave(
            db_session,
            employee_id=ctx.employee_id + 10_000,
            leave_type_id=ctx.leave_type_id,
            as_of=D(2026, 10, 24),
        )

    count = await db_session.execute(select(func.count()).select_from(LeaveBalanceEntry))
    assert count.scalar_one() == 0


async def test_fr_bal_001_accrual_is_refused_for_a_leave_type_that_is_not_the_main_one(
    db_session,
) -> None:
    ctx = await _hired(db_session)

    with pytest.raises(BalanceEntryError):
        await accrue_leave(
            db_session,
            employee_id=ctx.employee_id,
            leave_type_id=ctx.other_leave_type_id,  # unpaid
            as_of=D(2026, 10, 24),
        )

    assert await _events(db_session, ctx) == []


async def test_fr_bal_001_a_missing_leave_type_is_reported_as_missing_not_as_non_main(
    db_session,
) -> None:
    ctx = await _hired(db_session)

    with pytest.raises(BalanceEntryError, match="Вид отпуска не найден"):
        await accrue_leave(
            db_session,
            employee_id=ctx.employee_id,
            leave_type_id=10_000_000,
            as_of=D(2026, 10, 24),
        )

    assert await _events(db_session, ctx) == []


async def test_fr_bal_001_a_refused_leave_type_takes_no_lock_on_the_employee(db_session) -> None:
    # Проверка вида идёт до `FOR UPDATE`: отказ по виду не должен читать строку
    # сотрудника с блокировкой. Фиксируем порядок по запросам.
    from sqlalchemy import event

    ctx = await _hired(db_session)
    statements: list[str] = []
    connection = await db_session.connection()

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    sync_engine = connection.sync_engine
    event.listen(sync_engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(BalanceEntryError):
            await accrue_leave(
                db_session,
                employee_id=ctx.employee_id,
                leave_type_id=ctx.other_leave_type_id,
                as_of=D(2026, 10, 24),
            )
    finally:
        event.remove(sync_engine, "before_cursor_execute", capture)

    assert not any("FOR UPDATE" in statement.upper() for statement in statements)


async def test_fr_bal_001_a_manual_adjustment_is_not_counted_as_already_accrued(db_session) -> None:
    # Набор ALREADY_ACCRUED_ENTRY_TYPES - только `accrual`: корректировка не
    # уменьшает начисление (поведение зафиксировано, см. оговорку у константы).
    ctx = await _hired(db_session)
    await _adjust(db_session, ctx, YEAR_START, "5", D(2026, 5, 1))

    written = await _accrue(db_session, ctx, D(2026, 10, 24))

    assert written == Decimal("18.67")


def test_the_already_accrued_event_types_are_the_explicit_set_with_accrual_only() -> None:
    assert ALREADY_ACCRUED_ENTRY_TYPES == (ACCRUAL,)


async def test_fr_bal_001_a_calculation_date_before_the_hire_date_is_refused(db_session) -> None:
    ctx = await _hired(db_session)

    with pytest.raises(BalanceEntryError):
        await _accrue(db_session, ctx, D(2026, 3, 9))

    assert await _events(db_session, ctx) == []


async def test_br_8_balance_on_a_date_before_and_after_the_event(db_session) -> None:
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))

    def balance(as_of: datetime.date):
        return get_balance(
            db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START, as_of=as_of
        )

    assert await balance(D(2026, 10, 23)) == 0
    assert await balance(D(2026, 10, 24)) == Decimal("18.67")  # день события включается
    assert await balance(D(2026, 12, 31)) == Decimal("18.67")


async def test_br_8_balance_on_a_future_date_does_not_grow_without_an_accrual(db_session) -> None:
    # Граница из докстринга get_balance: функция отражает ЗАПИСАННОЕ.
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 10, 24))

    future = await get_balance(
        db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START, as_of=D(2027, 3, 1)
    )

    assert future == Decimal("18.67")  # не 28: начисления на эту дату в журнале нет


async def test_br_8_without_a_date_the_balance_is_all_events_as_before(db_session) -> None:
    ctx = await _hired(db_session)
    await _accrue(db_session, ctx, D(2026, 12, 10))

    assert await get_balance(db_session, ctx.employee_id, ctx.leave_type_id, YEAR_START) == (
        Decimal("21.00")
    )


async def _adjust(db_session, ctx: Context, year_start: datetime.date, quantity: str, on: D):
    return await adjust_balance(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=year_start,
        working_year_end=year_start.replace(year=year_start.year + 1),
        quantity=Decimal(quantity),
        reason="Перенос остатков",
        actor_account_id=ctx.actor_id,
        entry_date=on,
    )


async def test_br_8_total_balance_sums_every_working_year(db_session) -> None:
    ctx = await _hired(db_session)
    await _adjust(db_session, ctx, D(2024, 3, 10), "5", D(2025, 1, 10))
    await _adjust(db_session, ctx, D(2025, 3, 10), "12.5", D(2025, 6, 1))
    await _accrue(db_session, ctx, D(2026, 10, 24))

    by_year = await get_balances_by_working_year(db_session, ctx.employee_id, ctx.leave_type_id)
    total = await get_total_balance(db_session, ctx.employee_id, ctx.leave_type_id)

    assert by_year == {
        D(2024, 3, 10): Decimal("5"),
        D(2025, 3, 10): Decimal("12.5"),
        D(2026, 3, 10): Decimal("18.67"),
    }
    assert total == Decimal("36.17")


async def test_br_8_total_balance_on_a_date_counts_only_events_up_to_it(db_session) -> None:
    ctx = await _hired(db_session)
    await _adjust(db_session, ctx, D(2024, 3, 10), "5", D(2025, 1, 10))
    await _adjust(db_session, ctx, D(2025, 3, 10), "12.5", D(2025, 6, 1))

    total = await get_total_balance(
        db_session, ctx.employee_id, ctx.leave_type_id, as_of=D(2025, 3, 1)
    )

    assert total == Decimal("5")


async def test_br_8_total_balance_of_an_employee_without_events_is_zero(db_session) -> None:
    ctx = await _hired(db_session)

    assert await get_total_balance(db_session, ctx.employee_id, ctx.leave_type_id) == 0
