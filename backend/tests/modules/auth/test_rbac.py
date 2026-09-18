import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.security import create_access_token, create_refresh_token
from app.models.account import Account
from app.modules.auth.access_control import enforce_access
from app.modules.auth.exceptions import AccessDeniedError
from app.modules.auth.rbac import get_current_account, is_manager_of, require_roles
from tests.modules.auth.factories import make_account, make_employee


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_get_current_account_with_valid_token(db_session) -> None:
    employee = await make_employee(db_session, email="ivanov@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="ldap", role="hr_admin"
    )
    token = create_access_token(account_id=account.id, role=account.role)

    resolved = await get_current_account(credentials=_bearer(token), session=db_session)

    assert resolved.id == account.id


async def test_get_current_account_without_credentials_raises_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_account(credentials=None, session=None)

    assert exc_info.value.status_code == 401


async def test_get_current_account_rejects_refresh_token(db_session) -> None:
    employee = await make_employee(db_session, email="petrov@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="ldap", role="employee"
    )
    token = create_refresh_token(account_id=account.id)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_account(credentials=_bearer(token), session=db_session)

    assert exc_info.value.status_code == 401


async def test_get_current_account_rejects_inactive_account(db_session) -> None:
    # FR-AUTH-004 (PR #137 review, круг 4): `enforce_access` заворачивает
    # отключённую учётную запись в `AccessDeniedError` (403 через
    # обработчик в `main.py`), а не в `HTTPException` — так и вход, и
    # каждый следующий запрос отказывают одинаково.
    employee = await make_employee(db_session, email="smirnova@company.local")
    account = await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="ldap",
        role="employee",
        is_active=False,
    )
    token = create_access_token(account_id=account.id, role=account.role)

    with pytest.raises(AccessDeniedError):
        await get_current_account(credentials=_bearer(token), session=db_session)


async def test_get_current_account_rejects_deactivated_employee_card(db_session) -> None:
    # FR-AUTH-004 (PR #137 review, круг 4, замечание #1): токен остаётся
    # валидным и `accounts.is_active` не трогали — деактивация именно
    # карточки сотрудника должна отказывать на каждом запросе, а не
    # только при следующем входе.
    employee = await make_employee(
        db_session, email="deactivated@company.local", employment_status="terminated"
    )
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="ldap", role="employee"
    )
    token = create_access_token(account_id=account.id, role=account.role)

    with pytest.raises(AccessDeniedError):
        await get_current_account(credentials=_bearer(token), session=db_session)


async def test_enforce_access_rejects_missing_employee_card(db_session) -> None:
    # FR-AUTH-004 (PR #137 review, круг 4): карточка сотрудника отсутствует
    # (не то же самое, что деактивирована) — доступ тоже должен отказать.
    # Через `get_current_account`/сохранённый `Account` эту ветку не
    # воспроизвести: `accounts.employee_id` защищён FK на `employees.id`
    # (`app/models/account.py`), так что персистентная запись физически
    # не может ссылаться на несуществующую карточку. Вызываем
    # `enforce_access` — тот же код, что `get_current_account` выполняет
    # на каждом запросе, — прямо на непёрсистентном объекте.
    account = Account(employee_id=999_999, auth_type="ldap", role="employee", is_active=True)

    with pytest.raises(AccessDeniedError):
        await enforce_access(db_session, account)


async def test_get_current_account_allows_bootstrap_admin_without_employee_card(
    db_session,
) -> None:
    # Служебная запись без карточки сотрудника (`employee_id IS NULL`,
    # bootstrap-администратор, FR-AUTH-005) не должна ломаться проверкой,
    # которая читает таблицу `employees`.
    account = await make_account(
        db_session, employee_id=None, login="bootstrap", auth_type="local", role="system_admin"
    )
    token = create_access_token(account_id=account.id, role=account.role)

    resolved = await get_current_account(credentials=_bearer(token), session=db_session)

    assert resolved.id == account.id


async def test_require_roles_allows_matching_role(db_session) -> None:
    employee = await make_employee(db_session, email="fedorova@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="ldap", role="accountant"
    )

    dependency = require_roles("accountant", "hr_admin")
    resolved = await dependency(account=account)

    assert resolved is account


async def test_require_roles_denies_non_matching_role(db_session) -> None:
    employee = await make_employee(db_session, email="sysadmin@company.local")
    account = await make_account(
        db_session, employee_id=employee.id, auth_type="ldap", role="employee"
    )

    dependency = require_roles("hr_admin")

    with pytest.raises(HTTPException) as exc_info:
        await dependency(account=account)

    assert exc_info.value.status_code == 403


async def test_is_manager_of_true_for_direct_manager(db_session) -> None:
    manager = await make_employee(db_session, email="manager@company.local")
    subordinate = await make_employee(
        db_session, email="report@company.local", manager_id=manager.id
    )

    assert await is_manager_of(db_session, manager.id, subordinate.id) is True


async def test_is_manager_of_false_for_unrelated_employee(db_session) -> None:
    manager = await make_employee(db_session, email="manager2@company.local")
    other = await make_employee(db_session, email="other@company.local")

    assert await is_manager_of(db_session, manager.id, other.id) is False
