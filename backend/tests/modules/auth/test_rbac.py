import asyncio

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.security import create_access_token, create_refresh_token
from app.models.account import Account
from app.modules.auth.access_control import enforce_access
from app.modules.auth.exceptions import AccessDeniedError
from app.modules.auth.rbac import (
    get_current_account,
    is_direct_manager_of,
    is_in_management_chain,
    list_subordinate_employee_ids,
    require_roles,
)
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
    """`FR-AUTH-004/2` (учётная запись отключена)."""
    # FR-AUTH-004: `enforce_access` заворачивает отключённую учётную
    # запись в `AccessDeniedError` (403 через обработчик в `main.py`), а
    # не в `HTTPException` — так и вход, и каждый следующий запрос
    # отказывают одинаково.
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
    """`FR-AUTH-004/2` (карточка сотрудника деактивирована)."""
    # FR-AUTH-004: токен остаётся валидным и `accounts.is_active` не
    # трогали — деактивация именно карточки сотрудника должна отказывать
    # на каждом запросе, а не только при следующем входе.
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
    """`FR-AUTH-004/2` (карточка сотрудника отсутствует вовсе) -
    негативный сценарий, явно названный в задании `T-M1-23`."""
    # FR-AUTH-004: карточка сотрудника отсутствует (не то же самое, что
    # деактивирована) — доступ тоже должен отказать.
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
    """`FR-AUTH-004/3`: fixture uses `auth_type="ldap"`, but
    `require_roles`/`_dependency` above reads only `account.role` - it
    has no branch on `auth_type` at all, so this test (and the denial
    test below) already demonstrate the same matrix check a local
    account would get, not something specific to LDAP."""
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


async def test_is_direct_manager_of_true_for_direct_manager(db_session) -> None:
    manager = await make_employee(db_session, email="manager@company.local")
    subordinate = await make_employee(
        db_session, email="report@company.local", manager_id=manager.id
    )

    assert await is_direct_manager_of(db_session, manager.id, subordinate.id) is True


async def test_is_direct_manager_of_false_for_unrelated_employee(db_session) -> None:
    manager = await make_employee(db_session, email="manager2@company.local")
    other = await make_employee(db_session, email="other@company.local")

    assert await is_direct_manager_of(db_session, manager.id, other.id) is False


# --- is_in_management_chain / list_subordinate_employee_ids -------------
# «Своя команда» — весь путь вверх/вниз по цепочке `manager_id`, а не
# только прямое подчинение (plan.md §5.1).


async def test_is_in_management_chain_true_for_direct_manager(db_session) -> None:
    manager = await make_employee(db_session, email="chain-direct-manager@company.local")
    subordinate = await make_employee(
        db_session, email="chain-direct-report@company.local", manager_id=manager.id
    )

    assert await is_in_management_chain(db_session, manager.id, subordinate.id) is True


async def test_is_in_management_chain_true_for_two_levels_up(db_session) -> None:
    top = await make_employee(db_session, email="chain-top@company.local")
    middle = await make_employee(
        db_session, email="chain-middle@company.local", manager_id=top.id
    )
    bottom = await make_employee(
        db_session, email="chain-bottom@company.local", manager_id=middle.id
    )

    assert await is_in_management_chain(db_session, top.id, bottom.id) is True


async def test_is_in_management_chain_false_for_sibling_branch(db_session) -> None:
    top = await make_employee(db_session, email="chain-top2@company.local")
    branch_a_manager = await make_employee(
        db_session, email="chain-branch-a@company.local", manager_id=top.id
    )
    branch_b_manager = await make_employee(
        db_session, email="chain-branch-b@company.local", manager_id=top.id
    )
    leaf_in_branch_b = await make_employee(
        db_session, email="chain-leaf-b@company.local", manager_id=branch_b_manager.id
    )

    result = await is_in_management_chain(db_session, branch_a_manager.id, leaf_in_branch_b.id)
    assert result is False


async def test_is_in_management_chain_false_for_self(db_session) -> None:
    employee = await make_employee(db_session, email="chain-self@company.local")

    assert await is_in_management_chain(db_session, employee.id, employee.id) is False


async def test_is_in_management_chain_terminates_on_cyclic_data(db_session) -> None:
    # Цикл, вставленный напрямую в БД, в обход API: запрет циклов при
    # записи (T-M1-10/T-M1-15) — первая линия защиты, а не единственная,
    # потому что данные могут прийти иначе (например, будущий импорт,
    # T-M1-11). Запрашиваем сотрудника, которого в цикле A↔B нет вовсе —
    # `LIMIT 1` не может сработать на первой же строке (как сработал бы
    # для A или B), поэтому без `MAX_MANAGEMENT_CHAIN_DEPTH` обход
    # продолжался бы бесконечно (`UNION ALL` не схлопывает повторы —
    # `depth` растёт, «новых строк больше нет» никогда не наступает).
    # `asyncio.wait_for` превращает зависание в понятный красный тест
    # вместо бесконечного ожидания.
    employee_a = await make_employee(db_session, email="chain-cycle-a@company.local")
    employee_b = await make_employee(
        db_session, email="chain-cycle-b@company.local", manager_id=employee_a.id
    )
    employee_a.manager_id = employee_b.id
    await db_session.flush()
    unrelated = await make_employee(db_session, email="chain-cycle-unrelated@company.local")

    result = await asyncio.wait_for(
        is_in_management_chain(db_session, unrelated.id, employee_b.id), timeout=10
    )

    assert result is False


async def test_is_in_management_chain_false_for_self_with_cyclic_data(db_session) -> None:
    # PR review: цепочка вверх от A при цикле A<->B возвращается к A уже
    # на втором шаге, поэтому `is_in_management_chain(A, A)` без явной
    # проверки self-reference дал бы `True` — сотрудник с зацикленной
    # цепочкой мог бы «стать сам себе вышестоящим» и в M3 согласовать
    # собственную заявку вопреки FR-APPR-002.
    employee_a = await make_employee(db_session, email="chain-self-cycle-a@company.local")
    employee_b = await make_employee(
        db_session, email="chain-self-cycle-b@company.local", manager_id=employee_a.id
    )
    employee_a.manager_id = employee_b.id
    await db_session.flush()

    result = await asyncio.wait_for(
        is_in_management_chain(db_session, employee_a.id, employee_a.id), timeout=10
    )

    assert result is False


async def test_list_subordinate_employee_ids_returns_whole_subtree(db_session) -> None:
    top = await make_employee(db_session, email="subtree-top@company.local")
    middle = await make_employee(
        db_session, email="subtree-middle@company.local", manager_id=top.id
    )
    bottom = await make_employee(
        db_session, email="subtree-bottom@company.local", manager_id=middle.id
    )
    unrelated = await make_employee(db_session, email="subtree-unrelated@company.local")

    subordinate_ids = await list_subordinate_employee_ids(db_session, top.id)

    assert set(subordinate_ids) == {middle.id, bottom.id}
    assert top.id not in subordinate_ids
    assert unrelated.id not in subordinate_ids


async def test_list_subordinate_employee_ids_terminates_on_cyclic_data(db_session) -> None:
    # Тот же риск бесконечной рекурсии, что и у `is_in_management_chain`
    # (см. тест выше) — своя копия `MAX_MANAGEMENT_CHAIN_DEPTH` в этой
    # функции, свой тест.
    employee_a = await make_employee(db_session, email="subtree-cycle-a@company.local")
    employee_b = await make_employee(
        db_session, email="subtree-cycle-b@company.local", manager_id=employee_a.id
    )
    employee_a.manager_id = employee_b.id
    await db_session.flush()

    subordinate_ids = await asyncio.wait_for(
        list_subordinate_employee_ids(db_session, employee_a.id), timeout=10
    )

    # `employee_a` — сам `manager_employee_id`, не подчинённый себе: цикл
    # в данных не должен превращать его в собственного подчинённого.
    assert set(subordinate_ids) == {employee_b.id}
