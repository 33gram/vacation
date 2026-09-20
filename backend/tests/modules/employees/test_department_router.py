"""Интеграционные тесты `/api/departments*` (T-M1-10/T-M1-14, FR-EMP-002)."""

from __future__ import annotations

from sqlalchemy import select

from app.models.department import Department
from tests.modules.auth.factories import (
    bearer_header,
    make_account,
    make_department,
    make_employee,
)


async def _make_admin(db_session):
    employee = await make_employee(db_session, email=f"dept-admin{id(object())}@company.local")
    return await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="hr_admin"
    )


async def _make_employee_account(db_session):
    employee = await make_employee(db_session, email=f"dept-emp{id(object())}@company.local")
    return await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="employee"
    )


# --- GET /api/departments -------------------------------------------------


async def test_get_departments_lists_all_for_any_authenticated_role(db_session, api_client) -> None:
    await make_department(db_session, name="Root")
    account = await _make_employee_account(db_session)

    response = await api_client.get("/api/departments", headers=bearer_header(account))

    assert response.status_code == 200
    assert any(d["name"] == "Root" for d in response.json())


# --- POST /api/departments -------------------------------------------------


async def test_post_departments_requires_hr_admin_or_system_admin(db_session, api_client) -> None:
    account = await _make_employee_account(db_session)

    response = await api_client.post(
        "/api/departments", json={"name": "New"}, headers=bearer_header(account)
    )

    assert response.status_code == 403


async def test_post_departments_creates_without_head(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/departments", json={"name": "New department"}, headers=bearer_header(account)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["head_employee_id"] is None
    assert body["parent_id"] is None


async def test_post_departments_head_employee_id_in_body_is_ignored(db_session, api_client) -> None:
    # Схема создания не содержит `head_employee_id` вовсе — подразделение
    # создаётся без руководителя, даже если поле подсунуто в теле запроса.
    account = await _make_admin(db_session)
    employee = await make_employee(db_session, email="would-be-head@company.local")

    response = await api_client.post(
        "/api/departments",
        json={"name": "New department", "head_employee_id": employee.id},
        headers=bearer_header(account),
    )

    assert response.status_code == 201
    assert response.json()["head_employee_id"] is None


async def test_post_departments_with_nested_parent_succeeds(db_session, api_client) -> None:
    account = await _make_admin(db_session)
    parent = await make_department(db_session, name="Parent")

    response = await api_client.post(
        "/api/departments",
        json={"name": "Child", "parent_id": parent.id},
        headers=bearer_header(account),
    )

    assert response.status_code == 201
    assert response.json()["parent_id"] == parent.id


async def test_post_departments_rejects_nonexistent_parent(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.post(
        "/api/departments",
        json={"name": "Orphan", "parent_id": 999_999},
        headers=bearer_header(account),
    )

    assert response.status_code == 400


# --- PUT /api/departments/{id} ----------------------------------------------


async def test_put_departments_requires_hr_admin_or_system_admin(db_session, api_client) -> None:
    department = await make_department(db_session)
    account = await _make_employee_account(db_session)

    response = await api_client.put(
        f"/api/departments/{department.id}",
        json={"name": "Renamed"},
        headers=bearer_header(account),
    )

    assert response.status_code == 403


async def test_put_departments_updates_name_and_head(db_session, api_client) -> None:
    department = await make_department(db_session, name="Old name")
    head = await make_employee(db_session, email="new-head@company.local")
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/departments/{department.id}",
        json={"name": "New name", "head_employee_id": head.id},
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New name"
    assert body["head_employee_id"] == head.id


async def test_put_departments_returns_404_for_missing_department(db_session, api_client) -> None:
    account = await _make_admin(db_session)

    response = await api_client.put(
        "/api/departments/999999", json={"name": "Renamed"}, headers=bearer_header(account)
    )

    assert response.status_code == 404


async def test_put_departments_rejects_nonexistent_parent(db_session, api_client) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/departments/{department.id}",
        json={"name": "Renamed", "parent_id": 999_999},
        headers=bearer_header(account),
    )

    assert response.status_code == 400


async def test_put_departments_rejects_nonexistent_head(db_session, api_client) -> None:
    department = await make_department(db_session)
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/departments/{department.id}",
        json={"name": "Renamed", "head_employee_id": 999_999},
        headers=bearer_header(account),
    )

    assert response.status_code == 400


async def test_put_departments_rejects_terminated_head(db_session, api_client) -> None:
    # PR #139 review, замечание B: раньше проверялось только
    # существование сотрудника, не его employment_status.
    department = await make_department(db_session)
    terminated = await make_employee(
        db_session, email="terminated-head@company.local", employment_status="terminated"
    )
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/departments/{department.id}",
        json={"name": "Renamed", "head_employee_id": terminated.id},
        headers=bearer_header(account),
    )

    assert response.status_code == 400
    assert "head_employee_id" in response.json()["detail"]

    row = (
        await db_session.execute(select(Department).where(Department.id == department.id))
    ).scalar_one()
    assert row.head_employee_id is None


# --- Циклы запрещены (T-M1-10/T-M1-14) --------------------------------------


async def test_put_departments_rejects_self_as_parent(db_session, api_client) -> None:
    department = await make_department(db_session, name="Self")
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/departments/{department.id}",
        json={"name": "Self", "parent_id": department.id},
        headers=bearer_header(account),
    )

    assert response.status_code == 400


async def test_put_departments_rejects_three_node_cycle(db_session, api_client) -> None:
    department_a = await make_department(db_session, name="A")
    department_b = await make_department(db_session, name="B", parent_id=department_a.id)
    department_c = await make_department(db_session, name="C", parent_id=department_b.id)
    account = await _make_admin(db_session)

    # A -> B -> C уже существует; пытаемся сделать A потомком C.
    response = await api_client.put(
        f"/api/departments/{department_a.id}",
        json={"name": "A", "parent_id": department_c.id},
        headers=bearer_header(account),
    )

    assert response.status_code == 400
    row = (
        await db_session.execute(select(Department).where(Department.id == department_a.id))
    ).scalar_one()
    assert row.parent_id is None


async def test_put_departments_allows_arbitrary_nesting_without_cycle(
    db_session, api_client
) -> None:
    department_a = await make_department(db_session, name="A2")
    department_b = await make_department(db_session, name="B2", parent_id=department_a.id)
    department_c = await make_department(db_session, name="C2")
    account = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/departments/{department_c.id}",
        json={"name": "C2", "parent_id": department_b.id},
        headers=bearer_header(account),
    )

    assert response.status_code == 200
    assert response.json()["parent_id"] == department_b.id
