"""Сквозной сценарий развёртывания с нуля, только через API (T-M1-10/
T-M1-14/T-M1-15): вход bootstrap-администратором → смена первичного
пароля → создание подразделения без руководителя → создание первого
сотрудника без руководителя (вершина иерархии) → назначение его
руководителем подразделения → создание второго сотрудника → вход
второго сотрудника. Невозможность любого шага — дефект, а не
неудобство: пустая база, на которой Систему ещё никто не настраивал,
обязана пройти этот путь целиком.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.modules.auth.bootstrap import ensure_bootstrap_admin

NEW_BOOTSTRAP_PASSWORD = "FreshlyChangedPassword123!"
EMPLOYEE_TWO_PASSWORD = "SecondEmployeePassword456!"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_deploy_from_scratch_end_to_end(db_session, api_client) -> None:
    settings = get_settings()

    # 1. Первый запуск создаёт bootstrap-администратора (FR-AUTH-005) —
    # ровно то, что делает `lifespan` приложения при старте.
    await ensure_bootstrap_admin(db_session)

    # 2. Вход bootstrap-администратором.
    login_response = await api_client.post(
        "/api/auth/login/local",
        json={
            "login": settings.bootstrap_admin_login,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert login_response.status_code == 200, login_response.text
    login_body = login_response.json()
    assert login_body["must_change_password"] is True
    admin_token = login_body["access_token"]

    # 3. Смена первичного пароля — до неё `require_roles` не пускает ни
    # к чему, кроме `/me` и `/password/change` (T-M1-13).
    change_password_response = await api_client.post(
        "/api/auth/password/change",
        json={
            "current_password": settings.bootstrap_admin_password,
            "new_password": NEW_BOOTSTRAP_PASSWORD,
        },
        headers=_auth_headers(admin_token),
    )
    assert change_password_response.status_code == 204, change_password_response.text

    # Переполучаем токен: старый нёс `must_change_password=true` в
    # JWT-claim `role`, а бизнес-проверка читает актуальное состояние из
    # БД через `get_current_account` — токен как таковой не протух, но
    # логиним заново, как это сделал бы реальный клиент после экрана
    # смены пароля.
    relogin_response = await api_client.post(
        "/api/auth/login/local",
        json={"login": settings.bootstrap_admin_login, "password": NEW_BOOTSTRAP_PASSWORD},
    )
    assert relogin_response.status_code == 200, relogin_response.text
    relogin_body = relogin_response.json()
    assert relogin_body["must_change_password"] is False
    admin_token = relogin_body["access_token"]
    admin_headers = _auth_headers(admin_token)

    # 4. Подразделение создаётся без руководителя.
    department_response = await api_client.post(
        "/api/departments", json={"name": "Deploy-from-scratch Dept"}, headers=admin_headers
    )
    assert department_response.status_code == 201, department_response.text
    department = department_response.json()
    assert department["head_employee_id"] is None

    # 5. Первый сотрудник — вершина иерархии, без руководителя
    # (FR-EMP-001 в редакции v1.7).
    employee_one_response = await api_client.post(
        "/api/employees",
        json={
            "full_name": "Первый Тестовый Руководитель",
            "personnel_number": "DEPLOY-001",
            "department_id": department["id"],
            "position": "Директор",
            "hire_date": "2024-01-01",
            "email": "deploy-root@company.local",
            "work_schedule_type": "standard",
            "auth_type": "ldap",
        },
        headers=admin_headers,
    )
    assert employee_one_response.status_code == 201, employee_one_response.text
    employee_one = employee_one_response.json()
    assert employee_one["manager_id"] is None

    # 6. Назначаем его руководителем подразделения.
    assign_head_response = await api_client.put(
        f"/api/departments/{department['id']}",
        json={"name": department["name"], "head_employee_id": employee_one["id"]},
        headers=admin_headers,
    )
    assert assign_head_response.status_code == 200, assign_head_response.text
    assert assign_head_response.json()["head_employee_id"] == employee_one["id"]

    # 7. Второй сотрудник подчиняется первому; локальный вход, чтобы
    # можно было довести сценарий до реального логина без тестового LDAP.
    employee_two_response = await api_client.post(
        "/api/employees",
        json={
            "full_name": "Второй Тестовый Сотрудник",
            "personnel_number": "DEPLOY-002",
            "department_id": department["id"],
            "position": "Инженер",
            "hire_date": "2024-02-01",
            "manager_id": employee_one["id"],
            "email": "deploy-second@company.local",
            "work_schedule_type": "standard",
            "auth_type": "local",
            "initial_password": EMPLOYEE_TWO_PASSWORD,
        },
        headers=admin_headers,
    )
    assert employee_two_response.status_code == 201, employee_two_response.text
    employee_two = employee_two_response.json()
    assert employee_two["manager_id"] == employee_one["id"]

    # 8. Второй сотрудник входит в Систему.
    employee_two_login_response = await api_client.post(
        "/api/auth/login/local",
        json={"login": "deploy-second@company.local", "password": EMPLOYEE_TWO_PASSWORD},
    )
    assert employee_two_login_response.status_code == 200, employee_two_login_response.text
    employee_two_login_body = employee_two_login_response.json()
    assert employee_two_login_body["role"] == "employee"
    assert employee_two_login_body["must_change_password"] is True

    # Второй сотрудник видит свою карточку.
    me_response = await api_client.get(
        "/api/auth/me", headers=_auth_headers(employee_two_login_body["access_token"])
    )
    assert me_response.status_code == 200
    assert me_response.json()["employee_id"] == employee_two["id"]
