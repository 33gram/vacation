"""`POST /api/employees/import` (T-M1-11, FR-EMP-003) — обвязка эндпоинта
поверх `import_parsing`/`import_service`: авторизация, разбор
multipart-загрузки, отсутствие персональных данных в логе."""

from __future__ import annotations

import itertools
import logging

from sqlalchemy import select

from app.models.employee import Employee
from app.modules.employees import import_service
from app.modules.employees.import_parsing import EXPECTED_COLUMNS
from tests.fake_integrity_error import fake_integrity_error
from tests.modules.auth.factories import bearer_header, make_account, make_department, make_employee

_seq = itertools.count(1)

_HEADER_LINE = ",".join(EXPECTED_COLUMNS)


async def _make_admin(db_session):
    email = f"imp-admin{next(_seq)}@company.local"
    employee = await make_employee(db_session, email=email)
    return await make_account(
        db_session, employee_id=employee.id, auth_type="local", role="hr_admin"
    )


def _csv_file(*data_lines: str) -> tuple[str, bytes, str]:
    content = ("\n".join([_HEADER_LINE, *data_lines])).encode("utf-8")
    return ("employees.csv", content, "text/csv")


async def test_import_requires_role(db_session, api_client) -> None:
    employee = await make_employee(db_session, email="imp-plain@company.local")
    plain_account = await make_account(db_session, employee_id=employee.id, role="employee")

    csv_line = "Иван Иванов,P-1,IT,Инженер,2024-01-15,i@company.local,standard,,"
    response = await api_client.post(
        "/api/employees/import",
        files={"file": _csv_file(csv_line)},
        headers=bearer_header(plain_account),
    )

    assert response.status_code == 403


async def test_import_rejects_unsupported_file_extension(db_session, api_client) -> None:
    admin = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees/import",
        files={"file": ("employees.txt", b"whatever", "text/plain")},
        headers=bearer_header(admin),
    )

    assert response.status_code == 400


async def test_import_creates_employees_and_returns_summary(db_session, api_client) -> None:
    await make_department(db_session, name="IT")
    admin = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees/import",
        files={
            "file": _csv_file(
                "Иван Иванов,IMP-EP-1,IT,Инженер,2024-01-15,imp-ep-1@company.local,standard,,"
            )
        },
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted_count"] == 1
    assert body["rejected_count"] == 0
    assert body["errors"] == []

    row = (
        await db_session.execute(
            select(Employee).where(Employee.personnel_number == "IMP-EP-1")
        )
    ).scalar_one()
    assert row.full_name == "Иван Иванов"


async def test_import_reports_row_errors_without_500(db_session, api_client) -> None:
    await make_department(db_session, name="IT")
    admin = await _make_admin(db_session)

    response = await api_client.post(
        "/api/employees/import",
        files={
            "file": _csv_file(
                "Иван Иванов,IMP-EP-2,IT,Инженер,2024-01-15,imp-ep-2@company.local,standard,,",
                ",IMP-EP-3,IT,Инженер,2024-01-15,imp-ep-3@company.local,standard,,",
            )
        },
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted_count"] == 1
    assert body["rejected_count"] == 1
    assert body["errors"][0]["row_number"] == 3
    assert body["errors"][0]["field"] == "full_name"


async def test_import_does_not_log_personal_data(db_session, api_client, caplog) -> None:
    department = await make_department(db_session, name="IT")
    admin = await _make_admin(db_session)
    seq = next(_seq)
    full_name = "Секретный Импортированный Сотрудник"
    personnel_number = f"PII-IMP-{seq}"
    email = f"pii-imp-{seq}@company.local"
    # Строка файла сталкивается с уже существующим personnel_number —
    # сообщение об ошибке (`reason`) в ОТВЕТЕ содержит само значение
    # (`'{personnel_number}' already exists`), это нормально (ответ
    # видит загрузивший HR-администратор), но не должно попасть в лог.
    await make_employee(db_session, email=email, department_id=department.id, full_name=full_name)
    existing = (
        await db_session.execute(
            select(Employee).where(Employee.email == email)
        )
    ).scalar_one()
    personnel_number = existing.personnel_number

    with caplog.at_level(logging.DEBUG):
        response = await api_client.post(
            "/api/employees/import",
            files={
                "file": _csv_file(
                    f"{full_name},{personnel_number},IT,Инженер,2024-01-15,"
                    f"other-{seq}@company.local,standard,,",
                )
            },
            headers=bearer_header(admin),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted_count"] == 0
    assert body["rejected_count"] == 1
    # Значение реально попало в ОТВЕТ (иначе тест ничего не значил бы).
    assert personnel_number in body["errors"][0]["reason"]

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert full_name not in log_text
    assert personnel_number not in log_text
    assert email not in log_text


async def test_import_exhausted_write_retries_returns_400_not_500(
    db_session, api_client, monkeypatch, caplog
) -> None:
    """PR #139 review, коммит 1, п. 4: `import_employees` поднимает
    `ImportFileError`, исчерпав `_MAX_WRITE_ATTEMPTS` — раньше вызов
    `import_employees` был вне `try` в роутере, и это доходило до
    клиента как 500."""
    await make_department(db_session, name="IT")
    admin = await _make_admin(db_session)
    # Коммит освобождает SAVEPOINT, под которым созданы подразделение и
    # админ, — иначе `session.rollback()` в цикле повторов
    # `import_employees` (см. его докстринг) откатит и это тоже, и
    # вторая попытка не найдёт подразделение "IT".
    await db_session.commit()
    seq = next(_seq)
    full_name = "Секретный Импортированный Сотрудник 2"
    personnel_number = f"PII-IMP-RETRY-{seq}"
    email = f"pii-imp-retry-{seq}@company.local"

    async def _always_conflicts(*_args: object, **_kwargs: object) -> None:
        raise fake_integrity_error("employees_personnel_number_key")

    monkeypatch.setattr(import_service, "_write_accepted", _always_conflicts)

    with caplog.at_level(logging.DEBUG):
        response = await api_client.post(
            "/api/employees/import",
            files={
                "file": _csv_file(
                    f"{full_name},{personnel_number},IT,Инженер,2024-01-15,{email},standard,,"
                )
            },
            headers=bearer_header(admin),
        )

    assert response.status_code == 400
    assert "repeated concurrent write conflicts" in response.json()["detail"]

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert full_name not in log_text
    assert personnel_number not in log_text
    assert email not in log_text
