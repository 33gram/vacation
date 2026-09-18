from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.employee import Employee
from app.models.ldap_settings import LdapSettings


async def get_employee_by_email(session: AsyncSession, email: str) -> Employee | None:
    result = await session.execute(
        select(Employee).where(func.lower(Employee.email) == email.lower())
    )
    return result.scalar_one_or_none()


async def get_account_by_employee_id(session: AsyncSession, employee_id: int) -> Account | None:
    result = await session.execute(select(Account).where(Account.employee_id == employee_id))
    return result.scalar_one_or_none()


async def get_account_by_login(session: AsyncSession, login: str) -> Account | None:
    result = await session.execute(select(Account).where(Account.login == login))
    return result.scalar_one_or_none()


async def get_active_ldap_settings(session: AsyncSession) -> LdapSettings | None:
    result = await session.execute(select(LdapSettings).where(LdapSettings.is_active.is_(True)))
    return result.scalars().first()


async def count_active_system_admins(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Account)
        .where(Account.role == "system_admin", Account.is_active.is_(True))
    )
    return result.scalar_one()
