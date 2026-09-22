"""Локальная аутентификация по логину/e-mail и паролю (T-M1-08, FR-AUTH-002).

Логин — `employees.email` для сотрудников с явно назначенным локальным
входом либо `accounts.login` для служебных записей без карточки
сотрудника (например, bootstrap-администратор, FR-AUTH-005).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import exceeds_bcrypt_input_limit, verify_password
from app.core.time_utils import utcnow
from app.models.account import Account
from app.models.employee import Employee
from app.modules.auth.access_control import enforce_access
from app.modules.auth.exceptions import AccessDeniedError, AuthenticationError
from app.modules.auth.password_policy import (
    DEFAULT_LOCKOUT_DURATION,
    DEFAULT_MAX_FAILED_ATTEMPTS,
)
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


async def uses_local_auth(session: AsyncSession, login: str) -> bool:
    """Есть ли для `login` учётная запись с явно выбранным локальным
    паролем (`accounts.auth_type = 'local'`) — диспетчер
    `POST /api/auth/login` (T-M1-32) использует это, чтобы такие
    сотрудники продолжали входить через основной эндпоинт, даже когда
    LDAP настроен и подтверждён (FR-AUTH-003), а не только через
    `/api/auth/login/local`."""
    account, _ = await _resolve_local_account(session, login)
    return account is not None and account.auth_type == "local"


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

    if exceeds_bcrypt_input_limit(password):
        # PR #140 review (второй круг), FR-AUTH-002 (раздел 13, п. 20):
        # `MAX_PASSWORD_BYTES` раньше проверялся только при задании/смене
        # пароля (`validate_password_strength`), которую вход не
        # вызывает — `verify_password`/`bcrypt.checkpw` ниже усекает вход
        # так же, как хэширование усекает пароль при задании. Без этой
        # проверки легальный пароль ровно на границе плюс произвольный
        # хвост после неё аутентифицировал бы ту же учётную запись.
        # PR #140 review (третий круг, замечание 2б): отказ здесь — то же
        # СООБЩЕНИЕ и тот же СТАТУС (401), что и у обычного неверного
        # пароля, не более того. Ответ ЦЕЛИКОМ не неразличим: эта ветка
        # возвращается раньше обращения к БД и bcrypt, то есть быстрее
        # ветки «неверный пароль» ниже — по времени ответа отличима, см.
        # T-M1-41.
        raise AuthenticationError("Invalid login or password")

    account, employee = await _resolve_local_account(session, login)

    if account is None or account.auth_type != "local":
        # PR #140 review (третий круг, замечание 2а): то же СООБЩЕНИЕ и
        # тот же СТАТУС (401), что и у существующего логина с неверным
        # паролем, — но не то же самое по ВРЕМЕНИ ответа. Здесь bcrypt
        # не вызывается вовсе; в ветке `verify_password` ниже вызывается
        # всегда — разница порядка ~100 мс (стоимость bcrypt) против
        # микросекунд, которой достаточно, чтобы по времени ответа
        # отличить существующий логин от несуществующего. Закрывается
        # отдельно — T-M1-41 (сравнение с фиктивным хэшем фиксированной
        # стоимости), не этим отказом.
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
