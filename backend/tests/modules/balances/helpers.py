"""Общие хелперы тестов журнала остатка (не тесты - pytest их не собирает)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
