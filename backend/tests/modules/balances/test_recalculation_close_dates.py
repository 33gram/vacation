"""Критерий приёмки T-M2-32 (`BR-8`, `FR-BAL-003`): после смены даты приёма сумма остатков
по ВСЕМ рабочим годам на любую дату равна расчёту по НОВОЙ дате приёма, а остаток исчезнувшего
года на любую дату - нулю, потому что закрытие датировано днями прежних шагов, а не днём
пересчёта. Проверка - перебором КАЖДОГО дня от приёма до дня пересчёта.

Исходные данные - те же, что в измерении задачи: приём 10.03.2026, начислено на 24.10.2026,
пересчёт в тот же день.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.employee import Employee
from app.models.leave_balance_entry import ACCRUAL, RECALCULATION, LeaveBalanceEntry
from app.modules.balances.accrual import compute_accrual, working_year_starts
from app.modules.balances.service import (
    accrue_leave,
    get_balance,
    get_total_balance,
    recalculate_balance,
)
from tests.modules.balances.helpers import Context, make_context

D = datetime.date
ONE_DAY = datetime.timedelta(days=1)
OLD_HIRE = D(2026, 3, 10)
AS_OF = D(2026, 10, 24)

# (новая дата приёма, ожидается ли исчезновение прежнего года)
SHIFTS = [
    pytest.param(D(2026, 3, 11), True, id="+1 day"),
    pytest.param(D(2026, 3, 20), True, id="+10 days"),
    pytest.param(D(2026, 4, 10), True, id="+1 month"),
    pytest.param(D(2026, 6, 10), True, id="+3 months"),
    pytest.param(D(2025, 9, 10), True, id="-6 months"),
    pytest.param(D(2025, 3, 10), False, id="-1 year (whole years)"),
]


async def _hired(db_session, hire: datetime.date) -> Context:
    ctx = await make_context(db_session, "close")
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = hire
    employee.created_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    await db_session.flush()
    return ctx


async def _events(db_session, ctx: Context) -> list[LeaveBalanceEntry]:
    result = await db_session.execute(
        select(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == ctx.employee_id)
        .order_by(LeaveBalanceEntry.id)
    )
    return list(result.scalars())


async def _accrue(db_session, ctx: Context, as_of: datetime.date) -> None:
    await accrue_leave(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=as_of
    )


async def _shift(db_session, ctx: Context, old: datetime.date, new: datetime.date, **kw):
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = new
    await db_session.flush()
    return await recalculate_balance(
        db_session,
        employee_id=ctx.employee_id,
        actor_account_id=ctx.actor_id,
        as_of=kw.pop("as_of", AS_OF),
        changes={"hire_date": (old, new)},
    )


def _expected_total(hire: datetime.date, day: datetime.date) -> Decimal:
    """Сумма `compute_accrual` по всем рабочим годам новой даты приёма на `day`; для
    завершённого года расчёт берётся на его последний день."""
    starts = working_year_starts(hire, day)
    total = Decimal(0)
    for index, start in enumerate(starts):
        last_day = day if index == len(starts) - 1 else starts[index + 1] - ONE_DAY
        total += compute_accrual(
            hire_date=hire,
            as_of=last_day,
            annual_norm=28,
            rounding_threshold_days=15,
            excluded_days=0,
            working_year_start=start,
        ).accrued
    return total


def _days(first: datetime.date, last: datetime.date):
    day = first
    while day <= last:
        yield day
        day += ONE_DAY


@pytest.mark.parametrize(("new_hire", "vanishes"), SHIFTS)
async def test_br_8_the_total_on_every_day_equals_the_accrual_by_the_new_hire_date(
    db_session, new_hire, vanishes
) -> None:
    ctx = await _hired(db_session, OLD_HIRE)
    await _accrue(db_session, ctx, AS_OF)

    await _shift(db_session, ctx, OLD_HIRE, new_hire)

    for day in _days(min(OLD_HIRE, new_hire), AS_OF):
        total = await get_total_balance(
            db_session, ctx.employee_id, ctx.leave_type_id, as_of=day
        )
        assert total == _expected_total(new_hire, day), f"сумма по годам на {day}"
