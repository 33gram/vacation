"""Интеграционные тесты `/api/employees*` (T-M1-15, FR-EMP-001 в
редакции v1.7)."""

from __future__ import annotations

import itertools
import logging
from contextlib import contextmanager

import pytest
from sqlalchemy import event, select

from app.core.security import verify_password
from app.db.session import get_engine
from app.main import app
from app.models.account import Account
from app.models.employee import Employee
from tests.modules.auth.factories import (
    bearer_header,
    make_account,
    make_department,
    make_employee,
)

MIN_PASSWORD = "CorrectHorseBatteryStaple"


def test_employee_password_fields_are_marked_write_only() -> None:
    # NFR-SEC-001, симметрично LdapTestRequest (PR #137): пароль не
    # должен уходить в OpenAPI-схему (и сгенерированный TS-клиент) как
    # обычная строка.
    schema = app.openapi()["components"]["schemas"]
    for model_name, field_name in (
        ("EmployeeCreate", "initial_password"),
        ("EmployeeUpdate", "new_password"),
    ):
        field_schema = schema[model_name]["properties"][field_name]
        variants = field_schema.get("anyOf", [field_schema])
        assert any(variant.get("writeOnly") is True for variant in variants)


@contextmanager
def _count_sql_statements():
    """Число выполненных SQL-запросов на текущем движке — для проверки
    отсутствия N+1 (T-M1-10, `list_subordinate_employee_ids`)."""
    counter = {"n": 0}
    engine = get_engine()

    def _before_cursor_execute(*_args: object, **_kwargs: object) -> None:
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield counter
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)

_payload_seq = itertools.count(1)


async def _make_admin(db_session, *, role: str = "hr_admin"):
    employee = await make_employee(db_session, email=f"emp-admin{next(_payload_seq)}@company.local")
    return await make_account(db_session, employee_id=employee.id, auth_type="local", role=role)


def _employee_payload(**overrides: object) -> dict[str, object]:
    seq = next(_payload_seq)
    payload: dict[str, object] = {
        "full_name": "Тестовый Сотрудник",
        "personnel_number": f"P-{seq}",
        "position": "Инженер",
        "hire_date": "2024-01-15",
        "email": f"new-{seq}@company.local",
        "work_schedule_type": "standard",
    }
    payload.update(overrides)
    return payload


# --- POST /api/employees: required fields --------------------------------


async def test_create_employee_requires_role(db_session, api_client) -> None:
    department = await make_department(db_session)
    plain_employee = await make_employee(db_session, email="plain@company.local")
    employee_account = await make_account(
        db_session, employee_id=plain_employee.id, role="employee"
    )

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=department.id),
        headers=bearer_header(employee_account),
    )

    assert response.status_code == 403


async def test_create_employee_missing_fields_returns_full_list(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.post("/api/employees", json={}, headers=bearer_header(account))

    assert response.status_code == 422
    missing_fields = {error["loc"][-1] for error in response.json()["detail"]}
    assert {
        "full_name",
        "personnel_number",
        "department_id",
        "position",
        "hire_date",
        "email",
        "work_schedule_type",
    }.issubset(missing_fields)


async def test_create_employee_without_manager_succeeds(db_session, api_client) -> None:
    # FR-EMP-001 в редакции v1.7: вершина иерархии — руководитель не
    # обязателен.
    department = await make_department(db_session)
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=department.id),
        headers=bearer_header(account),
    )

    assert response.status_code == 201
    assert response.json()["manager_id"] is None


async def test_create_employee_rejects_nonexistent_department(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=999_999),
        headers=bearer_header(account),
    )

    assert response.status_code == 400


# --- POST /api/employees: duplicates ---------------------------------------


async def test_create_employee_rejects_duplicate_personnel_number(db_session, api_client) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)
    await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=department.id, personnel_number="DUP-1"),
        headers=bearer_header(account),
    )

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=department.id, personnel_number="DUP-1"),
        headers=bearer_header(account),
    )

    assert response.status_code == 400
    assert "personnel_number" in response.json()["detail"]


async def test_create_employee_rejects_duplicate_email_case_insensitive(
    db_session, api_client
) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)
    await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=department.id, email="Case@Company.Local"),
        headers=bearer_header(account),
    )

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=department.id, email="case@company.local"),
        headers=bearer_header(account),
    )

    assert response.status_code == 400
    assert "email" in response.json()["detail"]


# --- POST /api/employees: manager must be active ----------------------------


async def test_create_employee_rejects_terminated_manager(db_session, api_client) -> None:
    department = await make_department(db_session)
    terminated_manager = await make_employee(
        db_session, email="terminated-manager@company.local", employment_status="terminated"
    )
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=department.id, manager_id=terminated_manager.id),
        headers=bearer_header(account),
    )

    assert response.status_code == 400
    assert "manager_id" in response.json()["detail"]


# --- POST /api/employees: login method --------------------------------------


async def test_create_employee_with_local_auth_creates_account_atomically(
    db_session, api_client
) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(
            department_id=department.id,
            auth_type="local",
            initial_password=MIN_PASSWORD,
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 201
    body = response.json()
    assert "initial_password" not in body
    assert MIN_PASSWORD not in response.text

    new_employee_id = body["id"]
    new_account = (
        await db_session.execute(select(Account).where(Account.employee_id == new_employee_id))
    ).scalar_one()
    assert new_account.auth_type == "local"
    assert new_account.must_change_password is True
    assert new_account.role == "employee"
    assert new_account.local_password_hash is not None
    assert verify_password(MIN_PASSWORD, new_account.local_password_hash)


async def test_create_employee_local_auth_requires_password(db_session, api_client) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=department.id, auth_type="local"),
        headers=bearer_header(account),
    )

    assert response.status_code == 422


async def test_create_employee_local_auth_rejects_weak_password(db_session, api_client) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(
            department_id=department.id, auth_type="local", initial_password="short"
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 400


async def test_create_employee_ldap_auth_rejects_password(db_session, api_client) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(
            department_id=department.id, auth_type="ldap", initial_password=MIN_PASSWORD
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 422


async def test_create_employee_defaults_to_ldap_auth(db_session, api_client) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(department_id=department.id),
        headers=bearer_header(account),
    )

    assert response.status_code == 201
    new_account = (
        await db_session.execute(
            select(Account).where(Account.employee_id == response.json()["id"])
        )
    ).scalar_one()
    assert new_account.auth_type == "ldap"
    assert new_account.local_password_hash is None


# --- Atomicity: account creation failure must not leave an orphan card -----


async def test_create_employee_rolls_back_card_if_account_creation_fails(
    db_session, api_client, monkeypatch
) -> None:
    # httpx/ASGITransport re-raises unhandled exceptions instead of
    # turning them into a 500 response (unlike a real uvicorn server) —
    # `pytest.raises` is the correct way to observe the simulated
    # failure here; the point of the test is what happens to the DB row,
    # not the HTTP status.
    #
    # The failure has to land AFTER the employee card is flushed
    # (`session.flush()` runs before `Account(...)` is constructed) —
    # patching something that runs earlier, like `hash_password`, would
    # prove nothing: there would be no row to roll back yet.
    department = await make_department(db_session)
    account = await _make_admin(db_session)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated account-creation failure")

    monkeypatch.setattr("app.modules.employees.employee_service.Account", _boom)

    with pytest.raises(RuntimeError, match="simulated account-creation failure"):
        await api_client.post(
            "/api/employees",
            json=_employee_payload(department_id=department.id, personnel_number="ATOMIC-1"),
            headers=bearer_header(account),
        )

    row = (
        await db_session.execute(
            select(Employee).where(Employee.personnel_number == "ATOMIC-1")
        )
    ).scalar_one_or_none()
    assert row is None


# --- GET /api/employees/{id}: visibility ------------------------------------


async def test_get_employee_self_is_visible(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="self-view@company.local")
    account = await make_account(db_session, employee_id=employee.id, role="employee")

    response = await api_client.get(
        f"/api/employees/{employee.id}", headers=bearer_header(account)
    )

    assert response.status_code == 200


async def test_get_employee_unrelated_colleague_is_forbidden_for_plain_employee(
    db_session, api_client
) -> None:
    viewer = await make_employee(db_session, email="viewer@company.local")
    viewer_account = await make_account(db_session, employee_id=viewer.id, role="employee")
    other = await make_employee(db_session, email="unrelated-colleague@company.local")

    response = await api_client.get(
        f"/api/employees/{other.id}", headers=bearer_header(viewer_account)
    )

    assert response.status_code == 403


async def test_get_employee_manager_sees_two_levels_down(db_session, api_client) -> None:
    top = await make_employee(db_session, email="top-manager@company.local")
    top_account = await make_account(db_session, employee_id=top.id, role="employee")
    middle = await make_employee(
        db_session, email="middle-manager@company.local", manager_id=top.id
    )
    bottom = await make_employee(
        db_session, email="bottom-report@company.local", manager_id=middle.id
    )

    response = await api_client.get(
        f"/api/employees/{bottom.id}", headers=bearer_header(top_account)
    )

    assert response.status_code == 200


async def test_get_employee_manager_does_not_see_sibling_branch(db_session, api_client) -> None:
    top = await make_employee(db_session, email="top2@company.local")
    branch_a_manager = await make_employee(
        db_session, email="branch-a-manager@company.local", manager_id=top.id
    )
    branch_a_account = await make_account(
        db_session, employee_id=branch_a_manager.id, role="employee"
    )
    branch_b_manager = await make_employee(
        db_session, email="branch-b-manager@company.local", manager_id=top.id
    )
    leaf_in_branch_b = await make_employee(
        db_session, email="leaf-in-b@company.local", manager_id=branch_b_manager.id
    )

    response = await api_client.get(
        f"/api/employees/{leaf_in_branch_b.id}", headers=bearer_header(branch_a_account)
    )

    assert response.status_code == 403


async def test_get_employee_hr_admin_sees_anyone(db_session, api_client) -> None:
    other = await make_employee(db_session, email="hr-target@company.local")
    account = await _make_admin(db_session, role="hr_admin")

    response = await api_client.get(f"/api/employees/{other.id}", headers=bearer_header(account))

    assert response.status_code == 200


async def test_get_employee_accountant_sees_anyone_read_only(db_session, api_client) -> None:
    other = await make_employee(db_session, email="acct-target@company.local")
    account = await _make_admin(db_session, role="accountant")

    response = await api_client.get(f"/api/employees/{other.id}", headers=bearer_header(account))

    assert response.status_code == 200


async def test_put_employee_accountant_cannot_edit(db_session, api_client) -> None:
    department = await make_department(db_session)
    other = await make_employee(db_session, email="acct-edit-target@company.local")
    await make_account(db_session, employee_id=other.id, role="employee", auth_type="ldap")
    account = await _make_admin(db_session, role="accountant")

    response = await api_client.put(
        f"/api/employees/{other.id}",
        json=_employee_payload(department_id=department.id, auth_type="ldap"),
        headers=bearer_header(account),
    )

    assert response.status_code == 403


async def test_get_employee_returns_404_for_missing_employee(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.get("/api/employees/999999", headers=bearer_header(account))

    assert response.status_code == 404


# --- GET /api/employees: terminated filter ----------------------------------


async def test_get_employees_hides_terminated_by_default(db_session, api_client) -> None:
    await make_employee(db_session, email="active-1@company.local")
    await make_employee(
        db_session, email="gone@company.local", employment_status="terminated"
    )
    account = await _make_admin(db_session)

    response = await api_client.get("/api/employees", headers=bearer_header(account))

    assert response.status_code == 200
    emails = {e["email"] for e in response.json()}
    assert "gone@company.local" not in emails


async def test_get_employees_includes_terminated_when_requested(db_session, api_client) -> None:
    await make_employee(
        db_session, email="gone2@company.local", employment_status="terminated"
    )
    account = await _make_admin(db_session)

    response = await api_client.get(
        "/api/employees?include_terminated=true", headers=bearer_header(account)
    )

    assert response.status_code == 200
    emails = {e["email"] for e in response.json()}
    assert "gone2@company.local" in emails


async def test_get_employees_plain_employee_sees_only_self(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="only-self@company.local")
    account = await make_account(db_session, employee_id=employee.id, role="employee")
    await make_employee(db_session, email="stranger@company.local")

    response = await api_client.get("/api/employees", headers=bearer_header(account))

    assert response.status_code == 200
    ids = {e["id"] for e in response.json()}
    assert ids == {employee.id}


# --- PUT /api/employees/{id}: manager cycle prevention ----------------------


async def test_put_employee_rejects_self_as_manager(db_session, api_client) -> None:
    department = await make_department(db_session)
    employee = await make_employee(db_session, email="self-manager@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_employee_payload(
            department_id=department.id, manager_id=employee.id, auth_type="ldap"
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 400


async def test_put_employee_rejects_chain_cycle(db_session, api_client) -> None:
    department = await make_department(db_session)
    top = await make_employee(db_session, email="cycle-top@company.local")
    await make_account(db_session, employee_id=top.id, role="employee", auth_type="ldap")
    bottom = await make_employee(
        db_session, email="cycle-bottom@company.local", manager_id=top.id
    )
    account = await _make_admin(db_session)

    # top -> bottom уже существует; пытаемся сделать top подчинённым bottom.
    response = await api_client.put(
        f"/api/employees/{top.id}",
        json=_employee_payload(
            department_id=department.id, manager_id=bottom.id, auth_type="ldap"
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 400
    row = (
        await db_session.execute(select(Employee).where(Employee.id == top.id))
    ).scalar_one()
    assert row.manager_id is None


async def test_put_employee_reassigns_department_keeps_head_employee_id(
    db_session, api_client
) -> None:
    # Решение T-M1-10/T-M1-14: перевод сотрудника в другое подразделение
    # не снимает его автоматически с руководства прежним.
    old_department = await make_department(db_session, name="Old dept")
    new_department = await make_department(db_session, name="New dept")
    head = await make_employee(
        db_session, email="moving-head@company.local", department_id=old_department.id
    )
    await make_account(db_session, employee_id=head.id, role="employee", auth_type="ldap")
    old_department.head_employee_id = head.id
    await db_session.flush()
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/employees/{head.id}",
        json=_employee_payload(
            department_id=new_department.id,
            personnel_number=head.personnel_number,
            email=head.email,
            auth_type="ldap",
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    await db_session.refresh(old_department)
    assert old_department.head_employee_id == head.id


# --- No N+1 for a manager's team listing (T-M1-10) --------------------------


async def test_get_employees_query_count_does_not_grow_with_team_size(
    db_session, api_client
) -> None:
    manager = await make_employee(db_session, email="n1-manager@company.local")
    manager_account = await make_account(db_session, employee_id=manager.id, role="employee")
    for i in range(2):
        await make_employee(db_session, email=f"n1-small-{i}@company.local", manager_id=manager.id)

    with _count_sql_statements() as small_team_counter:
        small_response = await api_client.get(
            "/api/employees", headers=bearer_header(manager_account)
        )

    for i in range(15):
        await make_employee(db_session, email=f"n1-large-{i}@company.local", manager_id=manager.id)

    with _count_sql_statements() as large_team_counter:
        large_response = await api_client.get(
            "/api/employees", headers=bearer_header(manager_account)
        )

    assert small_response.status_code == 200
    assert large_response.status_code == 200
    assert len(large_response.json()) > len(small_response.json())
    assert large_team_counter["n"] == small_team_counter["n"]


# --- No personal data in logs -----------------------------------------------


async def test_create_employee_does_not_log_personal_data(db_session, api_client, caplog) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)
    seq = next(_payload_seq)
    full_name = "Секретный Тестовый Сотрудник"
    personnel_number = f"PII-{seq}"
    email = f"pii-{seq}@company.local"

    with caplog.at_level(logging.DEBUG):
        response = await api_client.post(
            "/api/employees",
            json=_employee_payload(
                department_id=department.id,
                full_name=full_name,
                personnel_number=personnel_number,
                email=email,
            ),
            headers=bearer_header(account),
        )

    assert response.status_code == 201
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert full_name not in log_text
    assert personnel_number not in log_text
    assert email not in log_text


# --- PUT /api/employees/{id}: switching the login method (T-M1-34) --------


async def test_put_employee_switch_to_ldap_clears_local_password(db_session, api_client) -> None:
    department = await make_department(db_session)
    employee = await make_employee(db_session, email="switch-to-ldap@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        role="employee",
        auth_type="local",
        password_hash="irrelevant-hash",
    )
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_employee_payload(
            department_id=department.id,
            personnel_number=employee.personnel_number,
            email=employee.email,
            auth_type="ldap",
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    updated_account = (
        await db_session.execute(select(Account).where(Account.employee_id == employee.id))
    ).scalar_one()
    assert updated_account.auth_type == "ldap"
    assert updated_account.local_password_hash is None
    assert updated_account.must_change_password is False


async def test_put_employee_switch_to_local_requires_password(db_session, api_client) -> None:
    department = await make_department(db_session)
    employee = await make_employee(db_session, email="switch-to-local-no-pw@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_employee_payload(
            department_id=department.id,
            personnel_number=employee.personnel_number,
            email=employee.email,
            auth_type="local",
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 400


async def test_put_employee_switch_to_local_sets_password_and_forces_change(
    db_session, api_client
) -> None:
    department = await make_department(db_session)
    employee = await make_employee(db_session, email="switch-to-local@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_employee_payload(
            department_id=department.id,
            personnel_number=employee.personnel_number,
            email=employee.email,
            auth_type="local",
            new_password=MIN_PASSWORD,
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    assert "new_password" not in response.json()
    assert MIN_PASSWORD not in response.text
    updated_account = (
        await db_session.execute(select(Account).where(Account.employee_id == employee.id))
    ).scalar_one()
    assert updated_account.auth_type == "local"
    assert updated_account.must_change_password is True
    assert updated_account.local_password_hash is not None
    assert verify_password(MIN_PASSWORD, updated_account.local_password_hash)


async def test_put_employee_staying_local_with_new_password_is_rejected(
    db_session, api_client
) -> None:
    # T-M1-34 меняет способ входа, а не сбрасывает пароль оставшемуся
    # локальным сотруднику (это отдельный механизм, T-M1-30) — молча
    # менять пароль мимо него нельзя.
    department = await make_department(db_session)
    employee = await make_employee(db_session, email="stay-local-reset@company.local")
    await make_account(
        db_session,
        employee_id=employee.id,
        role="employee",
        auth_type="local",
        password_hash="irrelevant-hash",
    )
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_employee_payload(
            department_id=department.id,
            personnel_number=employee.personnel_number,
            email=employee.email,
            auth_type="local",
            new_password=MIN_PASSWORD,
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 400


async def test_put_employee_switch_to_ldap_with_password_rejected_by_schema(
    db_session, api_client
) -> None:
    department = await make_department(db_session)
    employee = await make_employee(db_session, email="ldap-with-password@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="local")
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_employee_payload(
            department_id=department.id,
            personnel_number=employee.personnel_number,
            email=employee.email,
            auth_type="ldap",
            new_password=MIN_PASSWORD,
        ),
        headers=bearer_header(account),
    )

    assert response.status_code == 422
