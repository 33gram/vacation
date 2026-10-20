"""Параметры парольной политики и блокировки читаются из
`business_rules_config`, а не из кода (T-M2-09, `FR-AUTH-002/2`).

Каждый тест СМЕНЯЕТ значение параметра в таблице и проверяет, что
наблюдаемое поведение последовало за ним: именно это отличает чтение
параметра от совпадения сидированного значения с константой в коде.
Поведение при сидированных значениях (8 / 5 / 15) закрыто прежними
тестами парольной политики, которые этой задачей не правились.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select, text

from app.core.security import hash_password
from app.core.time_utils import utcnow
from app.models.account import Account
from app.modules.auth.exceptions import AuthenticationError
from app.modules.auth.local_service import authenticate_local
from app.modules.auth.password_policy import VIOLATION_TOO_SHORT
from tests.modules.auth.factories import (
    bearer_header,
    make_account,
    make_department,
    make_employee,
)

PASSWORD = "CorrectHorseBatteryStaple"
ELEVEN_CHARS = "Qw3!rTy9uIo"
TWELVE_CHARS = "Qw3!rTy9uIoP"


async def _set(db_session, key: str, value: str) -> None:
    await db_session.execute(
        text("UPDATE business_rules_config SET value = :value WHERE key = :key"),
        {"key": key, "value": value},
    )


async def _local_account(db_session, email: str) -> Account:
    employee = await make_employee(db_session, email=email)
    return await make_account(
        db_session,
        employee_id=employee.id,
        auth_type="local",
        role="employee",
        password_hash=hash_password(PASSWORD),
    )


async def test_lockout_threshold_and_duration_follow_the_parameters(db_session) -> None:
    await _set(db_session, "max_failed_login_attempts", "3")
    await _set(db_session, "lockout_duration_minutes", "30")
    account = await _local_account(db_session, "lockout-params@company.local")

    for _ in range(2):
        with pytest.raises(AuthenticationError):
            await authenticate_local(db_session, "lockout-params@company.local", "wrong-password")
    await db_session.refresh(account)
    assert account.locked_until is None

    with pytest.raises(AuthenticationError):
        await authenticate_local(db_session, "lockout-params@company.local", "wrong-password")
    await db_session.refresh(account)
    assert account.locked_until is not None
    remaining = account.locked_until - utcnow()
    assert datetime.timedelta(minutes=29) < remaining <= datetime.timedelta(minutes=30)


async def test_password_change_enforces_the_min_length_parameter(db_session, api_client) -> None:
    await _set(db_session, "password_min_length", "12")
    account = await _local_account(db_session, "min-length-change@company.local")

    too_short = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": PASSWORD, "new_password": ELEVEN_CHARS},
        headers=bearer_header(account),
    )
    long_enough = await api_client.post(
        "/api/auth/password/change",
        json={"current_password": PASSWORD, "new_password": TWELVE_CHARS},
        headers=bearer_header(account),
    )

    assert too_short.status_code == 400
    assert [v["code"] for v in too_short.json()["detail"]] == [VIOLATION_TOO_SHORT]
    assert "12" in too_short.json()["detail"][0]["message"]
    assert long_enough.status_code == 204


async def test_password_policy_hints_show_the_min_length_parameter(db_session, api_client) -> None:
    await _set(db_session, "password_min_length", "12")
    account = await _local_account(db_session, "min-length-hints@company.local")

    response = await api_client.get("/api/auth/password-policy", headers=bearer_header(account))

    hints = {h["code"]: h["hint"] for h in response.json()}
    assert "12" in hints[VIOLATION_TOO_SHORT]


async def test_employee_creation_enforces_the_min_length_parameter(db_session, api_client) -> None:
    from tests.modules.employees.test_employee_router import _employee_payload, _make_admin

    await _set(db_session, "password_min_length", "12")
    department = await make_department(db_session)
    admin = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees",
        json=_employee_payload(
            department_id=department.id, auth_type="local", initial_password=ELEVEN_CHARS
        ),
        headers=bearer_header(admin),
    )

    assert response.status_code == 400
    assert VIOLATION_TOO_SHORT in [v["code"] for v in response.json()["detail"]]


async def test_switching_to_local_login_enforces_the_min_length_parameter(
    db_session, api_client
) -> None:
    from tests.modules.employees.test_employee_router import _employee_payload, _make_admin

    await _set(db_session, "password_min_length", "12")
    department = await make_department(db_session)
    employee = await make_employee(db_session, email="switch-min-length@company.local")
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    admin = await _make_admin(db_session)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_employee_payload(
            department_id=department.id,
            personnel_number=employee.personnel_number,
            email=employee.email,
            full_name=employee.full_name,
            auth_type="local",
            new_password=ELEVEN_CHARS,
        ),
        headers=bearer_header(admin),
    )

    assert response.status_code == 400
    assert VIOLATION_TOO_SHORT in [v["code"] for v in response.json()["detail"]]
    unchanged = (
        await db_session.execute(select(Account).where(Account.employee_id == employee.id))
    ).scalar_one()
    assert unchanged.auth_type == "ldap"
