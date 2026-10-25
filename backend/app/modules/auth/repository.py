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


SYSTEM_AUTHOR_LABEL = "Система"
_ADMIN_AUTHOR_LABEL = "Администратор"
_USER_AUTHOR_LABEL = "Пользователь системы"


async def get_author_display_names(session: AsyncSession, account_ids: set[int]) -> dict[int, str]:
    """Подпись автора записи по аккаунту (`T-M2-11`): ФИО связанной карточки; у
    аккаунта БЕЗ карточки - НЕЙТРАЛЬНАЯ подпись (логин чужого пользователя в
    ответе рядовому сотруднику не нужен - ВЫБРАНО): «Администратор» для ролей
    `hr_admin`/`system_admin`, иначе «Пользователь системы». События без автора
    (начисляет система) подписывает вызывающий значением `SYSTEM_AUTHOR_LABEL`.
    Данные двух таблиц собирает этот модуль, слой API - нет."""
    if not account_ids:
        return {}
    rows = await session.execute(
        select(Account.id, Account.role, Employee.full_name)
        .outerjoin(Employee, Employee.id == Account.employee_id)
        .where(Account.id.in_(account_ids))
    )
    names: dict[int, str] = {}
    for row in rows:
        if row.full_name:
            names[row.id] = row.full_name
        elif row.role in ("hr_admin", "system_admin"):
            names[row.id] = _ADMIN_AUTHOR_LABEL
        else:
            names[row.id] = _USER_AUTHOR_LABEL
    return names
