"""Хелперы для создания тестовых employees/accounts. Не тесты — pytest их
не собирает (имя файла не начинается с test_)."""

from __future__ import annotations

import datetime
import itertools

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee

_personnel_number_seq = itertools.count(1)


async def make_employee(
    session: AsyncSession,
    *,
    email: str,
    full_name: str = "Тестовый Сотрудник",
    department_id: int | None = None,
    manager_id: int | None = None,
    employment_status: str = "active",
) -> Employee:
    employee = Employee(
        full_name=full_name,
        personnel_number=f"T-{next(_personnel_number_seq)}",
        department_id=department_id,
        position="Инженер",
        hire_date=datetime.date(2020, 1, 1),
        manager_id=manager_id,
        email=email,
        employment_status=employment_status,
    )
    session.add(employee)
    await session.flush()
    return employee


async def make_account(
    session: AsyncSession,
    *,
    employee_id: int | None = None,
    login: str | None = None,
    auth_type: str = "local",
    role: str = "employee",
    password_hash: str | None = None,
    is_active: bool = True,
    must_change_password: bool = False,
) -> Account:
    account = Account(
        employee_id=employee_id,
        login=login,
        auth_type=auth_type,
        local_password_hash=password_hash,
        role=role,
        is_active=is_active,
        must_change_password=must_change_password,
    )
    session.add(account)
    await session.flush()
    return account


async def make_department(
    session: AsyncSession,
    *,
    name: str = "Тестовое подразделение",
    parent_id: int | None = None,
    head_employee_id: int | None = None,
) -> Department:
    department = Department(name=name, parent_id=parent_id, head_employee_id=head_employee_id)
    session.add(department)
    await session.flush()
    return department


def bearer_header(account: Account) -> dict[str, str]:
    token = create_access_token(account_id=account.id, role=account.role)
    return {"Authorization": f"Bearer {token}"}
