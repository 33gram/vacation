"""Общие хелперы тестов журнала остатка (не тесты - pytest их не собирает)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.balances.accrual import compute_accrual, working_year_starts
from tests.modules.auth.factories import make_account, make_employee

YEAR_START = datetime.date(2026, 1, 1)
YEAR_END = datetime.date(2027, 1, 1)
EVENT_DATE = datetime.date(2026, 3, 1)


@dataclass(frozen=True)
class Context:
    employee_id: int
    leave_type_id: int
    other_leave_type_id: int
    actor_id: int
    other_actor_id: int


async def make_context(session: AsyncSession, suffix: str = "a") -> Context:
    """Сотрудник, два автора (аккаунты HR) и два вида из сидирования `0006`."""
    employee = await make_employee(session, email=f"balance-{suffix}@company.local")
    hr_one = await make_account(session, login=f"hr-one-{suffix}", role="hr_admin")
    hr_two = await make_account(session, login=f"hr-two-{suffix}", role="hr_admin")
    ids = {
        row.code: row.id for row in await session.execute(text("SELECT id, code FROM leave_types"))
    }
    return Context(
        employee_id=employee.id,
        leave_type_id=ids["annual_paid"],
        other_leave_type_id=ids["unpaid"],
        actor_id=hr_one.id,
        other_actor_id=hr_two.id,
    )


def expected_total_accrual(hire: datetime.date, day: datetime.date) -> Decimal:
    """Сумма `compute_accrual` по всем рабочим годам даты приёма `hire` на `day`; для завершённого
    года расчёт берётся на его последний день. Норма 28 и порог 15 - сидирование `0011`.

    ДУБЛЬ НАМЕРЕННЫЙ и единственная НЕЗАВИСИМАЯ проверка суммы по годам (`T-M2-33`): тесты
    `get_total_balance` (`test_recalculation_close_dates.py`) и прогноза
    (`test_balance_forecast.py`) сверяют промышленный код с ЭТОЙ функцией. Она считает по
    `compute_accrual` с явным началом года и НЕ вызывает ни `accrue_leave`, ни `forecast_accrual`,
    ни `get_total_balance`. Заменить её вызовом промышленного кода - значит сравнивать код с
    самим собой: тесты перестанут проверять что-либо. Её же поведение закреплено жёсткими
    числами в `test_balance_forecast.py`."""
    one_day = datetime.timedelta(days=1)
    starts = working_year_starts(hire, day)
    total = Decimal(0)
    for index, start in enumerate(starts):
        last_day = day if index == len(starts) - 1 else starts[index + 1] - one_day
        total += compute_accrual(
            hire_date=hire,
            as_of=last_day,
            annual_norm=28,
            rounding_threshold_days=15,
            excluded_days=0,
            working_year_start=start,
        ).accrued
    return total
