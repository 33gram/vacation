"""Сетка уровня приложения на неожиданный `IntegrityError` (PR #139
review, коммит 1): параметризовано по трём ЗНАНЫМ путям записи в
`employees`/`accounts` — создание карточки, правка карточки, импорт.

Это НЕ доказательство для всех возможных путей записи. В отличие от
`test_endpoint_auth_coverage.py`, которая перечисляет эндпоинты по
самой OpenAPI-схеме приложения (значит, покрывает и любой будущий), тут
перечислить нечего: схема не говорит, какие из эндпоинтов пишут именно
в `employees`/`accounts` и потому подвержены гонке на `personnel_
number`/`email`. Тест фиксирует три сегодняшних писателя; четвёртый,
который появится позже и забудет про `unique_violations`, этот тест
не поймает — но поймает обработчик `IntegrityError` в `main.py`
(проверено отдельно, `test_main_exception_handlers.py`), который и есть
настоящая гарантия, а не этот файл.
"""

from __future__ import annotations

import itertools
import logging

import pytest

from tests.fake_integrity_error import fake_integrity_error
from tests.modules.auth.factories import bearer_header, make_account, make_department, make_employee

_seq = itertools.count(1)


async def _make_admin(db_session):
    email = f"safety-admin{next(_seq)}@company.local"
    employee = await make_employee(db_session, email=email)
    return await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="hr_admin"
    )


async def _raise_unexpected_integrity_error(*_args: object, **_kwargs: object) -> None:
    # Имя вне карты `unique_violations.UNIQUE_CONSTRAINT_FIELDS` —
    # проверяет именно сетку в `main.py`, а не перевод в поле-именованный
    # `ValueError` (это уже покрыто прежними раундами тестов).
    raise fake_integrity_error("some_unrelated_constraint")


@pytest.mark.parametrize("path_name", ["create", "update", "import"])
async def test_write_path_never_500s_on_unexpected_integrity_error(
    path_name, db_session, api_client, monkeypatch, caplog
) -> None:
    department = await make_department(db_session)
    admin = await _make_admin(db_session)
    n = next(_seq)
    full_name = f"Secret Person {n}"
    personnel_number = f"SAFETY-{n}"
    email = f"safety-{n}@company.local"

    existing_id: int | None = None
    if path_name == "update":
        existing = await make_employee(
            db_session, email=f"existing-{n}@company.local", department_id=department.id
        )
        await make_account(db_session, employee_id=existing.id, role="employee", auth_type="ldap")
        existing_id = existing.id

    monkeypatch.setattr(db_session, "commit", _raise_unexpected_integrity_error)

    with caplog.at_level(logging.DEBUG):
        if path_name == "create":
            response = await api_client.post(
                "/api/employees",
                json={
                    "full_name": full_name,
                    "personnel_number": personnel_number,
                    "department_id": department.id,
                    "position": "Инженер",
                    "hire_date": "2024-01-15",
                    "email": email,
                    "work_schedule_type": "standard",
                },
                headers=bearer_header(admin),
            )
        elif path_name == "update":
            response = await api_client.put(
                f"/api/employees/{existing_id}",
                json={
                    "full_name": full_name,
                    "personnel_number": personnel_number,
                    "department_id": department.id,
                    "position": "Инженер",
                    "hire_date": "2024-01-15",
                    "email": email,
                    "work_schedule_type": "standard",
                    "auth_type": "ldap",
                },
                headers=bearer_header(admin),
            )
        else:
            header = (
                "full_name,personnel_number,department,position,hire_date,email,"
                "work_schedule_type,manager_personnel_number,leave_category"
            )
            row = (
                f"{full_name},{personnel_number},{department.name},Инженер,2024-01-15,"
                f"{email},standard,,"
            )
            content = f"{header}\n{row}\n".encode()
            response = await api_client.post(
                "/api/employees/import",
                files={"file": ("safety.csv", content, "text/csv")},
                headers=bearer_header(admin),
            )

    assert response.status_code == 409, response.text

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert full_name not in log_text
    assert personnel_number not in log_text
    assert email not in log_text
