"""Конкурентный импорт двух отдельных файлов с общим `personnel_number`
(PR #139 review, замечание 1 оси спецификации): раньше `import_service.
_write_accepted` коммитил без обработки `IntegrityError`, а роутер ловил
только `ImportFileError` — конкурентная гонка на TOCTOU-окне фазы 1 (обе
стороны читают "personnel_number свободен" до того, как другая
закоммитила) давала необработанный 500 с трассировкой, содержащей ФИО и
e-mail проигравшей строки.

Используется фикстура `client` (НЕ `api_client`): у нeё `get_db_session`
не переопределён, поэтому оба запроса на импорт идут через настоящую
FastAPI-зависимость с независимыми соединениями — обязательное условие
для настоящей гонки (см. `test_concurrent_employee_duplicates.py`).

Синхронизация — простой одновременный старт запросов (`asyncio.
gather`), БЕЗ рандеву внутри `import_service._load_existing`: после PR
#139 review, замечания C (`import_employees` теперь берёт `lock_
management_chain` на всю фазу 1+2, см. докстринг `import_service.py`)
такое рандеву внутри `_load_existing` привело бы к взаимной блокировке
— сторона, выигравшая замок, дошла бы до `_load_existing` первой и
ждала бы вторую строго внутри него, а вторая сторона в это время сама
блокирована на `pg_advisory_xact_lock`, ожидая, пока первая не
закоммитит (проверено прогоном: именно так и произошло — deadlock,
видимый как зависшая `idle in transaction` строка в `pg_stat_
activity`). Простой одновременный старт этого не требует: блокировка
теперь сама гарантирует правильный результат при любом порядке
планирования, а не только при специально форсированном перекрытии, —
разница с гонками в `test_concurrent_org_changes.py`, где блокировки
на ОБЕИХ проверяемых путях нет по определению (это и есть отрицательный
контроль) и синхронизация нужна, чтобы гонка вообще проявилась.

Маркер на прогон во всех естественных ключах; очистка в `finally` по
маркеру, а не по конкретным id.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.db.session import get_engine
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.modules.employees.import_parsing import EXPECTED_COLUMNS


async def _make_hr_admin_token(marker: str) -> tuple[int, int]:
    engine = get_engine()
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        department = Department(name=f"Import Race Dept {marker}")
        session.add(department)
        await session.flush()
        admin_employee = Employee(
            full_name="Import Race Admin",
            personnel_number=f"IMPRACE-ADMIN-{marker}",
            department_id=department.id,
            position="HR",
            hire_date=datetime.date(2024, 1, 1),
            email=f"import-race-admin-{marker}@company.local",
            work_schedule_type="standard",
        )
        session.add(admin_employee)
        await session.flush()
        admin_account = Account(employee_id=admin_employee.id, auth_type="ldap", role="hr_admin")
        session.add(admin_account)
        await session.commit()
        account_id = admin_account.id
        department_id = department.id
        await session.close()
    return account_id, department_id


async def _cleanup(marker: str, department_id: int, personnel_numbers: list[str]) -> None:
    engine = get_engine()
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        rows = (
            await session.execute(
                Employee.__table__.select().where(
                    or_(
                        Employee.personnel_number.in_(personnel_numbers),
                        Employee.personnel_number == f"IMPRACE-ADMIN-{marker}",
                    )
                )
            )
        ).all()
        ids = [row.id for row in rows]
        await session.execute(Account.__table__.delete().where(Account.employee_id.in_(ids)))
        await session.execute(Employee.__table__.delete().where(Employee.id.in_(ids)))
        await session.execute(
            Department.__table__.delete().where(Department.id == department_id)
        )
        await session.commit()
        await session.close()


def _csv_bytes(department_name: str, personnel_number: str, full_name: str, email: str) -> bytes:
    header = ",".join(EXPECTED_COLUMNS)
    row = f"{full_name},{personnel_number},{department_name},Инженер,2024-01-15,{email},standard,,"
    return f"{header}\n{row}\n".encode()


async def test_concurrent_import_with_colliding_personnel_number_never_500(
    client, caplog
) -> None:
    marker = uuid.uuid4().hex[:12]
    account_id, department_id = await _make_hr_admin_token(marker)
    token = create_access_token(account_id=account_id, role="hr_admin")
    headers = {"Authorization": f"Bearer {token}"}
    department_name = f"Import Race Dept {marker}"
    shared_personnel_number = f"IMPRACE-SHARED-{marker}"

    full_name_a = "Race Import Side A"
    email_a = f"race-import-a-{marker}@company.local"
    full_name_b = "Race Import Side B"
    email_b = f"race-import-b-{marker}@company.local"

    file_a = _csv_bytes(department_name, shared_personnel_number, full_name_a, email_a)
    file_b = _csv_bytes(department_name, shared_personnel_number, full_name_b, email_b)

    try:
        with caplog.at_level(logging.DEBUG):
            responses = await asyncio.gather(
                client.post(
                    "/api/employees/import",
                    files={"file": ("a.csv", file_a, "text/csv")},
                    headers=headers,
                ),
                client.post(
                    "/api/employees/import",
                    files={"file": ("b.csv", file_b, "text/csv")},
                    headers=headers,
                ),
            )

        statuses = [r.status_code for r in responses]
        assert 500 not in statuses, [r.text for r in responses]
        assert statuses == [200, 200]

        bodies = [r.json() for r in responses]
        assert sum(b["accepted_count"] for b in bodies) == 1
        assert sum(b["rejected_count"] for b in bodies) == 1

        rejected_body = next(b for b in bodies if b["rejected_count"] == 1)
        assert rejected_body["errors"][0]["field"] == "personnel_number"
        assert shared_personnel_number in rejected_body["errors"][0]["reason"]

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        for value in (full_name_a, email_a, full_name_b, email_b, shared_personnel_number):
            assert value not in log_text
    finally:
        await _cleanup(marker, department_id, [shared_personnel_number])
