"""Единые проверки доступа после успешной аутентификации (T-M1-27,
FR-AUTH-004) — вызываются одинаково из ldap_service и local_service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.employee import Employee
from app.modules.auth.exceptions import AccessDeniedError


async def enforce_access(
    session: AsyncSession, account: Account, *, employee: Employee | None = None
) -> None:
    if not account.is_active:
        raise AccessDeniedError("Account is deactivated")

    if account.employee_id is not None:
        if employee is None:
            employee = await session.get(Employee, account.employee_id)
        if employee is None or employee.employment_status != "active":
            raise AccessDeniedError("Employee record is missing or deactivated")
