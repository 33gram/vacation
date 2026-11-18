"""Критерий приёмки T-M2-21 (`BR-8`, `FR-BAL-001`): остаток на ЛЮБУЮ дату рабочего года
равен накопленному по `compute_accrual`, потому что начисление записано по событию на
шаг, датированному днём, когда право возникло. Проверка - перебором КАЖДОГО дня года, а
не выбранных дат. Арифметика шагов - `test_accrual_arithmetic.py`; сервис по-старому
(суммы, повторные вызовы) - `test_accrual_service.py`.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models.employee import Employee
from app.models.leave_balance_entry import ACCRUAL, INITIAL_IMPORT, LeaveBalanceEntry
from app.modules.balances.accrual import compute_accrual, working_year_start_on
from app.modules.balances.service import (
    accrue_all_working_years,
    accrue_leave,
    get_balance,
)
from tests.modules.balances.helpers import Context, make_context

D = datetime.date
ONE_DAY = datetime.timedelta(days=1)


async def _setup(
    db_session, hire: datetime.date, *, norm: int = 28, threshold: int = 15
) -> Context:
    for key, value in (("annual_leave_days", norm), ("month_rounding_threshold_days", threshold)):
        await db_session.execute(
            text("UPDATE business_rules_config SET value = :value WHERE key = :key"),
            {"value": str(value), "key": key},
        )
    ctx = await make_context(db_session, "steps")
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = hire
    employee.created_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)  # барьер не мешает
    await db_session.flush()
    return ctx


async def _events(db_session, ctx: Context) -> list[LeaveBalanceEntry]:
    result = await db_session.execute(
        select(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == ctx.employee_id)
        .order_by(LeaveBalanceEntry.id)
    )
    return list(result.scalars())


async def _balance(db_session, ctx: Context, start: datetime.date, on: datetime.date) -> Decimal:
    return await get_balance(
        db_session, ctx.employee_id, ctx.leave_type_id, start, as_of=on
    )


def _expected(hire, start, on, *, norm, threshold, excluded=0) -> Decimal:
    return compute_accrual(
        hire_date=hire,
        as_of=on,
        annual_norm=norm,
        rounding_threshold_days=threshold,
        excluded_days=excluded,
        working_year_start=start,
    ).accrued


@pytest.mark.parametrize(
    ("hire", "year_index", "norm", "threshold", "excluded"),
    [
        (D(2026, 3, 10), 0, 28, 15, 0),  # обычный год
        (D(2025, 1, 31), 0, 28, 15, 0),  # приём 31-го числа
        (D(2023, 1, 31), 1, 28, 15, 0),  # приём 31-го, в году февраль 2024 (високосный)
        (D(2024, 2, 29), 0, 28, 15, 0),  # приём 29 февраля
        (D(2024, 2, 29), 1, 28, 15, 0),  # следующий год, в котором 29 февраля нет
        (D(2023, 3, 10), 1, 28, 15, 0),  # обычная дата, год содержит 29.02.2024
        (D(2026, 3, 10), 0, 29, 15, 0),  # норма не делится на 12
        (D(2026, 3, 10), 0, 29, 1, 0),  # крайний порог: 1
        (D(2026, 3, 10), 0, 29, 31, 0),  # крайний порог: 31
        (D(2026, 3, 10), 0, 28, 15, 5),  # исключаемые дни сдвигают шаги
    ],
)
async def test_br_8_balance_on_every_day_of_the_year_equals_the_accrual_of_that_day(
    db_session, hire, year_index, norm, threshold, excluded
) -> None:
    ctx = await _setup(db_session, hire, norm=norm, threshold=threshold)
    start = working_year_start_on(hire, year_index)
    end = working_year_start_on(hire, year_index + 1) + datetime.timedelta(days=excluded)

    # Начисление ОДИН раз, на последний день года - и остаток на каждую дату внутри года.
    await accrue_leave(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=end - ONE_DAY,
        excluded_days=excluded,
        working_year_start=start,
    )

    day = start
    while day < end:
        assert await _balance(db_session, ctx, start, day) == _expected(
            hire, start, day, norm=norm, threshold=threshold, excluded=excluded
        ), f"остаток на {day}"
        day += ONE_DAY


@pytest.mark.parametrize("norm", [28, 29, 30])
async def test_the_step_amounts_add_up_to_the_norm(db_session, norm) -> None:
    # Двенадцать округлённых по норме / 12 не дали бы норму (28 -> 27,96; 29 -> 29,04);
    # количество шага - разность округлённых накопленных.
    ctx = await _setup(db_session, D(2026, 3, 10), norm=norm)

    await accrue_leave(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=D(2027, 3, 9),
    )

    events = await _events(db_session, ctx)
    assert len(events) == 12 and all(e.quantity > 0 for e in events)
    assert sum(e.quantity for e in events) == Decimal(norm)


async def test_a_repeated_call_writes_nothing_and_a_call_a_year_later_writes_only_new_steps(
    db_session,
) -> None:
    ctx = await _setup(db_session, D(2026, 3, 10))
    call = {"employee_id": ctx.employee_id, "leave_type_id": ctx.leave_type_id}
    await accrue_leave(db_session, **call, as_of=D(2026, 8, 1))  # 5 шагов: 24.03 ... 24.07
    assert len(await _events(db_session, ctx)) == 5

    assert await accrue_leave(db_session, **call, as_of=D(2026, 8, 1)) == 0  # та же дата
    assert len(await _events(db_session, ctx)) == 5

    ids_before = {e.id for e in await _events(db_session, ctx)}
    year_later = await accrue_leave(
        db_session, **call, as_of=D(2027, 3, 9), working_year_start=D(2026, 3, 10)
    )  # через год: дописываются только оставшиеся семь шагов
    events = await _events(db_session, ctx)
    assert len(events) == 12 and ids_before <= {e.id for e in events}
    assert year_later == Decimal("28.00") - Decimal("11.67")
    dates = [e.entry_date for e in events]
    assert dates == sorted(dates) and len(set(dates)) == 12  # ни одного дубля шага


async def test_a_year_accrued_in_one_piece_before_is_completed_by_new_steps_not_rewritten(
    db_session,
) -> None:
    # Данные, записанные ДО T-M2-21: одно событие 18,67, датированное днём записи. Журнал
    # добавляемый, его не переписывают: недостающие шаги дописываются разностью, а остаток на
    # даты до дня старого события остаётся таким, каким его показывало записанное.
    ctx = await _setup(db_session, D(2026, 3, 10))
    start = D(2026, 3, 10)
    lump = LeaveBalanceEntry(
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=start,
        working_year_end=D(2027, 3, 10),
        entry_type=ACCRUAL,
        quantity=Decimal("18.67"),
        entry_date=D(2026, 10, 24),
        reason="Начисление на 24.10.2026: 8 из 12 месяцев рабочего года",
    )
    db_session.add(lump)
    await db_session.flush()

    written = await accrue_leave(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=D(2027, 3, 9),
    )

    events = await _events(db_session, ctx)
    assert written == Decimal("9.33") and len(events) == 5  # старое + четыре шага (9-й ... 12-й)
    assert events[0].id == lump.id and events[0].quantity == Decimal("18.67")  # не тронуто
    assert await _balance(db_session, ctx, start, D(2027, 3, 9)) == Decimal("28.00")
    # Остаток на дату ДО старого события занижен - ровно как раньше; шаги за него не пишутся.
    assert await _balance(db_session, ctx, start, D(2026, 9, 24)) == 0


async def test_a_terminated_employee_stops_growing_on_the_termination_date(db_session) -> None:
    hire, start = D(2026, 3, 10), D(2026, 3, 10)
    ctx = await _setup(db_session, hire)
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.employment_status = "terminated"
    employee.termination_date = D(2026, 7, 10)
    await db_session.flush()

    await accrue_all_working_years(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=D(2027, 3, 9),
    )

    day = start
    while day < D(2027, 3, 10):
        capped = min(day, D(2026, 7, 10))
        assert await _balance(db_session, ctx, start, day) == _expected(
            hire, start, capped, norm=28, threshold=15
        ), f"остаток на {day}"
        day += ONE_DAY
    assert await _balance(db_session, ctx, start, D(2027, 3, 9)) == Decimal("9.33")  # 4 шага


async def test_a_year_with_an_initial_balance_accrues_only_the_steps_after_its_date(
    db_session,
) -> None:
    # `FR-BAL-001/4`, решение заказчика `T-M1-36`: начальный остаток - остаток НА СВОЮ ДАТУ.
    # Шаги года с датой не позже 01.05.2026 (24.03 и 24.04: 2 месяца, 4,67) покрыты им и не
    # пишутся; шаги 3-12 начисляются обычным порядком, итого 28,00 - 4,67 = 23,33. Остаток на
    # день: 0 до даты остатка, с неё 12,5 плюс накопленное по шагам СВЕРХ покрытых.
    hire, start = D(2026, 3, 10), D(2026, 3, 10)
    ctx = await _setup(db_session, hire)
    db_session.add(
        LeaveBalanceEntry(
            employee_id=ctx.employee_id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=start,
            working_year_end=D(2027, 3, 10),
            entry_type=INITIAL_IMPORT,
            quantity=Decimal("12.5"),
            entry_date=D(2026, 5, 1),
            reason="Начальный остаток",
            actor_account_id=ctx.actor_id,
        )
    )
    await db_session.flush()

    result = await accrue_all_working_years(
        db_session,
        employee_id=ctx.employee_id,
        leave_type_id=ctx.leave_type_id,
        as_of=D(2027, 3, 9),
    )

    assert result.accrued == {start: Decimal("23.33")} and result.skipped == {}
    types = [e.entry_type for e in await _events(db_session, ctx)]
    assert types == [INITIAL_IMPORT] + [ACCRUAL] * 10
    day = start
    covered = Decimal("4.67")
    while day < D(2027, 3, 10):
        if day < D(2026, 5, 1):
            expected = Decimal(0)
        else:
            cumulative = _expected(hire, start, day, norm=28, threshold=15)
            expected = Decimal("12.5") + max(Decimal(0), cumulative - covered)
        assert await _balance(db_session, ctx, start, day) == expected, f"остаток на {day}"
        day += ONE_DAY
