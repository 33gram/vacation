"""`POST /api/employees/{id}/deactivate` (T-M1-12, FR-EMP-004 без
фиксации остатка — см. `T-M1-37`)."""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.models.ldap_settings import LdapSettings
from app.modules.auth.exceptions import AccessDeniedError
from app.modules.auth.ldap_service import authenticate_via_ldap
from tests.ldap_guard import LDAP_TEST_HOST, LDAP_TEST_PORT, requires_ldap
from tests.modules.auth.factories import bearer_header, make_account, make_department, make_employee

_payload_seq = itertools.count(1)

LDAP_TEST_BASE_DN = "dc=company,dc=local"
LDAP_TEST_ADMIN_DN = "cn=admin,dc=company,dc=local"
LDAP_TEST_ADMIN_PASSWORD = "changeme"
LDAP_TEST_USER_PASSWORD = "TestPass123!"


async def _make_admin(db_session, *, role: str = "hr_admin"):
    email = f"deact-admin{next(_payload_seq)}@company.local"
    employee = await make_employee(db_session, email=email)
    return await make_account(db_session, employee_id=employee.id, auth_type="local", role=role)


async def test_deactivate_employee_succeeds(db_session, api_client) -> None:
    department = await make_department(db_session)
    employee = await make_employee(
        db_session, email="deact-target1@company.local", department_id=department.id
    )
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    admin = await _make_admin(db_session)

    response = await api_client.post(
        f"/api/employees/{employee.id}/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["employment_status"] == "terminated"
    assert body["termination_date"] == "2026-09-19"

    row = (
        await db_session.execute(select(Employee).where(Employee.id == employee.id))
    ).scalar_one()
    assert row.employment_status == "terminated"
    assert row.termination_date.isoformat() == "2026-09-19"

    account_row = (
        await db_session.execute(select(Account).where(Account.employee_id == employee.id))
    ).scalar_one()
    assert account_row.is_active is False


async def test_deactivate_employee_requires_role(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="deact-target2@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    plain_employee = await make_employee(db_session, email="deact-plain@company.local")
    plain_account = await make_account(db_session, employee_id=plain_employee.id, role="employee")

    response = await api_client.post(
        f"/api/employees/{employee.id}/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(plain_account),
    )

    assert response.status_code == 403


async def test_deactivate_employee_returns_404_for_missing_employee(db_session, api_client) -> None:
    admin = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees/999999/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(admin),
    )

    assert response.status_code == 404


async def test_deactivate_employee_rejects_active_direct_reports(db_session, api_client) -> None:
    manager = await make_employee(db_session, email="deact-manager1@company.local")
    await make_account(db_session, employee_id=manager.id, role="employee", auth_type="ldap")
    subordinate = await make_employee(
        db_session, email="deact-sub1@company.local", manager_id=manager.id
    )
    await make_account(db_session, employee_id=subordinate.id, role="employee", auth_type="ldap")
    admin = await _make_admin(db_session)

    response = await api_client.post(
        f"/api/employees/{manager.id}/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(admin),
    )

    assert response.status_code == 400
    assert "manager_id" in response.json()["detail"]

    row = (await db_session.execute(select(Employee).where(Employee.id == manager.id))).scalar_one()
    assert row.employment_status == "active"


async def test_deactivate_employee_allows_terminated_direct_reports(db_session, api_client) -> None:
    # Отказ — только для АКТИВНЫХ подчинённых; уже уволенный подчинённый
    # не должен блокировать деактивацию бывшего руководителя.
    manager = await make_employee(db_session, email="deact-manager2@company.local")
    await make_account(db_session, employee_id=manager.id, role="employee", auth_type="ldap")
    await make_employee(
        db_session,
        email="deact-sub2@company.local",
        manager_id=manager.id,
        employment_status="terminated",
    )
    admin = await _make_admin(db_session)

    response = await api_client.post(
        f"/api/employees/{manager.id}/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(admin),
    )

    assert response.status_code == 200


async def test_deactivate_employee_rejects_department_head(db_session, api_client) -> None:
    department = await make_department(db_session)
    head = await make_employee(
        db_session, email="deact-head1@company.local", department_id=department.id
    )
    await make_account(db_session, employee_id=head.id, role="employee", auth_type="ldap")
    department.head_employee_id = head.id
    await db_session.flush()
    admin = await _make_admin(db_session)

    response = await api_client.post(
        f"/api/employees/{head.id}/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(admin),
    )

    assert response.status_code == 400
    assert "head_employee_id" in response.json()["detail"]

    row = (
        await db_session.execute(select(Department).where(Department.id == department.id))
    ).scalar_one()
    assert row.head_employee_id == head.id


async def test_deactivate_employee_twice_is_rejected_cleanly(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="deact-target3@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    admin = await _make_admin(db_session)

    first = await api_client.post(
        f"/api/employees/{employee.id}/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(admin),
    )
    assert first.status_code == 200

    second = await api_client.post(
        f"/api/employees/{employee.id}/deactivate",
        json={"termination_date": "2026-09-20"},
        headers=bearer_header(admin),
    )

    assert second.status_code == 400
    # Устойчивый признак — id сотрудника, а не формулировка сообщения
    # (T-M1-47: сообщение теперь по-русски и меняется по мере правок текста).
    assert str(employee.id) in second.json()["detail"]


async def test_deactivated_employee_is_hidden_from_default_list(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="deact-target4@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    admin = await _make_admin(db_session)

    deactivate_response = await api_client.post(
        f"/api/employees/{employee.id}/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(admin),
    )
    assert deactivate_response.status_code == 200

    default_list = await api_client.get("/api/employees", headers=bearer_header(admin))
    assert employee.id not in [e["id"] for e in default_list.json()]

    full_list = await api_client.get(
        "/api/employees", params={"include_terminated": True}, headers=bearer_header(admin)
    )
    assert employee.id in [e["id"] for e in full_list.json()]


async def test_deactivated_employee_cannot_login_locally(db_session, api_client) -> None:
    password = "CorrectHorseBatteryStaple"
    employee = await make_employee(db_session, email="deact-local@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        role="employee",
        auth_type="local",
        password_hash=hash_password(password),
    )
    admin = await _make_admin(db_session)

    deactivate_response = await api_client.post(
        f"/api/employees/{employee.id}/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(admin),
    )
    assert deactivate_response.status_code == 200

    login_response = await api_client.post(
        "/api/auth/login/local",
        json={"login": "deact-local@company.local", "password": password},
    )

    assert login_response.status_code == 403


@requires_ldap
async def test_deactivated_employee_cannot_login_via_ldap(db_session, api_client) -> None:
    settings_row = LdapSettings(
        server_url=LDAP_TEST_HOST,
        port=LDAP_TEST_PORT,
        base_dn=LDAP_TEST_BASE_DN,
        bind_dn=LDAP_TEST_ADMIN_DN,
        bind_password_encrypted=encrypt(
            LDAP_TEST_ADMIN_PASSWORD, key=get_settings().ldap_password_encryption_key
        ),
        user_match_attribute="mail",
        is_active=True,
    )
    db_session.add(settings_row)
    await db_session.flush()

    employee = await make_employee(db_session, email="smirnova@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    admin = await _make_admin(db_session)

    # Деактивация через настоящий эндпоинт (не прямая правка ORM) — тест
    # проверяет, что именно ЭТОТ эндпоинт блокирует последующий вход, а
    # не только что `employment_status = 'terminated'` само по себе
    # (это уже покрыто `test_ldap_service.py`).
    deactivate_response = await api_client.post(
        f"/api/employees/{employee.id}/deactivate",
        json={"termination_date": "2026-09-19"},
        headers=bearer_header(admin),
    )
    assert deactivate_response.status_code == 200

    with pytest.raises(AccessDeniedError):
        await authenticate_via_ldap(db_session, "smirnova@company.local", LDAP_TEST_USER_PASSWORD)
