"""Нарушение уникальности под конкурентной нагрузкой (PR review): 40
одновременных запросов, сталкивающихся на одном и том же
`personnel_number`/`email`, не должны отдавать необработанный
`IntegrityError`/500 — ровно один обязан пройти, остальные — понятный
отказ с указанием поля, тем же кодом, что и у обычной (не гоночной)
проверки.

Используется фикстура `client` (НЕ `api_client`): у неё `get_db_session`
не переопределён, поэтому каждый запрос идёт через настоящую
FastAPI-зависимость и получает СВОЁ соединение из пула — только так
воспроизводится TOCTOU-гонка предварительной проверки
(`_require_personnel_number_free`/`_require_email_free` читают
согласованное состояние до записи конкурентов, поэтому у всех 40 запросов
предварительная проверка проходит одинаково "чисто").
"""

from __future__ import annotations

import asyncio
import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.db.session import get_engine
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.modules.employees import employee_service

CONCURRENCY = 40


async def _make_hr_admin_token() -> tuple[int, int]:
    engine = get_engine()
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        department = Department(name="Dup Race Dept")
        session.add(department)
        await session.flush()
        admin_employee = Employee(
            full_name="Dup Race Admin",
            personnel_number="DUPRACE-ADMIN",
            department_id=department.id,
            position="HR",
            hire_date=datetime.date(2024, 1, 1),
            email="dup-race-admin@company.local",
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


async def _cleanup(department_id: int, personnel_numbers: list[str]) -> None:
    engine = get_engine()
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        rows = (
            await session.execute(
                Employee.__table__.select().where(
                    Employee.personnel_number.in_(personnel_numbers + ["DUPRACE-ADMIN"])
                )
            )
        ).all()
        ids = [row.id for row in rows]
        await session.execute(Account.__table__.delete().where(Account.employee_id.in_(ids)))
        await session.execute(Employee.__table__.delete().where(Employee.id.in_(ids)))
        await session.execute(Department.__table__.delete().where(Department.id == department_id))
        await session.commit()
        await session.close()


async def test_concurrent_creates_with_colliding_email_never_500(client) -> None:
    account_id, department_id = await _make_hr_admin_token()
    token = create_access_token(account_id=account_id, role="hr_admin")
    headers = {"Authorization": f"Bearer {token}"}
    personnel_numbers = [f"DUPRACE-EMAIL-{i}" for i in range(CONCURRENCY)]

    try:
        # Один и тот же e-mail в разном регистре — уникальность
        # регистронезависима (`ix_employees_email_lower`).
        payloads = [
            {
                "full_name": f"Dup Race {i}",
                "personnel_number": personnel_numbers[i],
                "department_id": department_id,
                "position": "Engineer",
                "hire_date": "2024-01-01",
                "email": f"{'DupRaceEmail' if i % 2 else 'dupraceemail'}@Company.Local",
                "work_schedule_type": "standard",
            }
            for i in range(CONCURRENCY)
        ]

        responses = await asyncio.gather(
            *(client.post("/api/employees", json=p, headers=headers) for p in payloads)
        )

        statuses = [r.status_code for r in responses]
        assert 500 not in statuses, statuses
        assert statuses.count(201) == 1, statuses
        assert statuses.count(400) == CONCURRENCY - 1, statuses
        for response in responses:
            if response.status_code == 400:
                assert "email" in response.json()["detail"]
    finally:
        await _cleanup(department_id, personnel_numbers)


async def test_concurrent_creates_with_colliding_personnel_number_never_500(client) -> None:
    account_id, department_id = await _make_hr_admin_token()
    token = create_access_token(account_id=account_id, role="hr_admin")
    headers = {"Authorization": f"Bearer {token}"}
    shared_personnel_number = "DUPRACE-SHARED-NUMBER"

    try:
        payloads = [
            {
                "full_name": f"Dup Race PN {i}",
                "personnel_number": shared_personnel_number,
                "department_id": department_id,
                "position": "Engineer",
                "hire_date": "2024-01-01",
                "email": f"dup-race-pn-{i}@company.local",
                "work_schedule_type": "standard",
            }
            for i in range(CONCURRENCY)
        ]

        responses = await asyncio.gather(
            *(client.post("/api/employees", json=p, headers=headers) for p in payloads)
        )

        statuses = [r.status_code for r in responses]
        assert 500 not in statuses, statuses
        assert statuses.count(201) == 1, statuses
        assert statuses.count(400) == CONCURRENCY - 1, statuses
        for response in responses:
            if response.status_code == 400:
                assert "personnel_number" in response.json()["detail"]
    finally:
        await _cleanup(department_id, [shared_personnel_number])


async def test_concurrent_updates_with_colliding_personnel_number_never_500(
    client, monkeypatch
) -> None:
    # В отличие от двух тестов выше (`POST`, где создающиеся карточки
    # неизбежно сериализуются на `INSERT` новой строки), здесь update
    # уже существующих строк может пройти достаточно быстро, чтобы 40
    # запросов на практике не перекрылись без принудительной задержки —
    # тест был бы нестабильным. Так же, как в `test_concurrent_org_
    # changes.py`: после прохождения опережающей проверки каждый запрос
    # ждёт, отдавая управление остальным, — гарантированное перекрытие
    # вместо везения планировщика.
    original_require_personnel_number_free = employee_service._require_personnel_number_free

    async def _delayed_require_personnel_number_free(*args, **kwargs):
        await original_require_personnel_number_free(*args, **kwargs)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(
        employee_service,
        "_require_personnel_number_free",
        _delayed_require_personnel_number_free,
    )

    account_id, department_id = await _make_hr_admin_token()
    token = create_access_token(account_id=account_id, role="hr_admin")
    headers = {"Authorization": f"Bearer {token}"}
    personnel_numbers = [f"DUPRACE-PUT-{i}" for i in range(CONCURRENCY)]
    target_personnel_number = "DUPRACE-PUT-TARGET"

    engine = get_engine()
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        employees = [
            Employee(
                full_name=f"Dup Race PUT {i}",
                personnel_number=personnel_numbers[i],
                department_id=department_id,
                position="Engineer",
                hire_date=datetime.date(2024, 1, 1),
                email=f"dup-race-put-{i}@company.local",
                work_schedule_type="standard",
            )
            for i in range(CONCURRENCY)
        ]
        session.add_all(employees)
        await session.flush()
        session.add_all(
            [Account(employee_id=e.id, auth_type="ldap", role="employee") for e in employees]
        )
        await session.commit()
        employee_ids = [e.id for e in employees]
        await session.close()

    try:
        # Все 40 одновременно пытаются взять ОДИН и тот же
        # personnel_number — предварительная проверка у всех пройдёт
        # чисто (никто из них его ещё не занял), настоящая гонка — на
        # `COMMIT`.
        responses = await asyncio.gather(
            *(
                client.put(
                    f"/api/employees/{employee_ids[i]}",
                    json={
                        "full_name": f"Dup Race PUT {i}",
                        "personnel_number": target_personnel_number,
                        "department_id": department_id,
                        "position": "Engineer",
                        "hire_date": "2024-01-01",
                        "email": f"dup-race-put-{i}@company.local",
                        "work_schedule_type": "standard",
                        "auth_type": "ldap",
                    },
                    headers=headers,
                )
                for i in range(CONCURRENCY)
            )
        )

        statuses = [r.status_code for r in responses]
        assert 500 not in statuses, statuses
        assert statuses.count(200) == 1, statuses
        assert statuses.count(400) == CONCURRENCY - 1, statuses
        for response in responses:
            if response.status_code == 400:
                assert "personnel_number" in response.json()["detail"]
    finally:
        await _cleanup(department_id, [*personnel_numbers, target_personnel_number])
