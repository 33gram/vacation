"""Локальная аутентификация по логину/e-mail и паролю (T-M1-08, FR-AUTH-002).

Логин — `employees.email` для сотрудников с явно назначенным локальным
входом либо `accounts.login` для служебных записей без карточки
сотрудника (например, bootstrap-администратор, FR-AUTH-005).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.core.time_utils import utcnow
from app.models.account import Account
from app.models.employee import Employee
from app.modules.auth.access_control import enforce_access
from app.modules.auth.exceptions import AccessDeniedError, AuthenticationError
from app.modules.auth.password_policy import DEFAULT_LOCKOUT_DURATION, DEFAULT_MAX_FAILED_ATTEMPTS
from app.modules.auth.repository import (
    get_account_by_employee_id,
    get_account_by_login,
    get_employee_by_email,
)
from app.modules.auth.tokens import AuthResult, issue_tokens


async def _resolve_local_account(
    session: AsyncSession, login: str
) -> tuple[Account | None, Employee | None]:
    employee = await get_employee_by_email(session, login)
    if employee is not None:
        account = await get_account_by_employee_id(session, employee.id)
        return account, employee

    account = await get_account_by_login(session, login)
    return account, None


async def _register_failed_attempt(session: AsyncSession, account: Account) -> None:
    account.failed_login_attempts += 1
    if account.failed_login_attempts >= DEFAULT_MAX_FAILED_ATTEMPTS:
        account.locked_until = utcnow() + DEFAULT_LOCKOUT_DURATION
    await session.commit()


async def authenticate_local(session: AsyncSession, login: str, password: str) -> AuthResult:
    if not password or not password.strip():
        # Пустой/пробельный пароль отклоняется до любого обращения к БД или
        # каталогу (FR-AUTH-004) — см. ldap_service.authenticate_via_ldap.
        raise AuthenticationError("Invalid login or password")

    account, employee = await _resolve_local_account(session, login)

    if account is None or account.auth_type != "local":
        # Не различаем «нет такого логина» и «неверный пароль» — исключаем
        # перебор существующих логинов (FR-AUTH-002).
        raise AuthenticationError("Invalid login or password")

    if account.locked_until is not None and account.locked_until > utcnow():
        raise AuthenticationError("Account temporarily locked, try again later")

    if account.local_password_hash is None or not verify_password(
        password, account.local_password_hash
    ):
        await _register_failed_attempt(session, account)
        raise AuthenticationError("Invalid login or password")

    account.failed_login_attempts = 0
    account.locked_until = None

    try:
        await enforce_access(session, account, employee=employee)
    except AccessDeniedError:
        await session.commit()
        raise

    await session.commit()
    return issue_tokens(account)
