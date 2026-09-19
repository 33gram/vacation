"""Нарушение уникальности под конкурентной нагрузкой (PR review): 40
одновременных запросов, сталкивающихся на одном и том же
`personnel_number`/`email`, не должны отдавать необработанный
`IntegrityError`/500 — ровно один обязан пройти, остальные — понятный
отказ с указанием поля, тем же кодом, что и у обычной (не гоночной)
проверки.

Используется фикстура `client` (НЕ `api_client`): у неё `get_db_session`
не переопределён по умолчанию, поэтому каждый запрос идёт через
настоящую FastAPI-зависимость. `_synchronized_race` временно
переопределяет её на движок с пулом, вмещающим весь `CONCURRENCY`
одновременно (у пула SQLAlchemy по умолчанию — 15 соединений
(`pool_size=5` + `max_overflow=10`), поэтому 40 запросов иначе
физически не могут все одновременно держать соединение — они
поступают волнами, и внутри волны один почти всегда успевает
закоммитить раньше, чем следующая волна прочитает состояние: race не
воспроизводится, тест проходит, ничего не проверив).

Барьер после опережающей проверки (не на старт запроса и не
фиксированной задержкой) гарантирует, что ВСЕ участники прочитали
"поле свободно" до того, как ХОТЬ ОДИН продолжил к записи — только так
воспроизводится TOCTOU-гонка предварительной проверки
(`_require_personnel_number_free`/`_require_email_free`), а не
последовательность из отдельных пар «проверил → записал».

Все естественные ключи (`personnel_number`, `email`, имя подразделения)
несут уникальный для прогона маркер, а очистка в `finally` ищет строки
по этому маркеру, а не по фиксированным значениям — иначе остаток от
упавшего посередине прогона ломает не только повторный запуск этого
теста, но и соседние тесты, считающие строки в таблице.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Awaitable, Iterable
from contextlib import asynccontextmanager

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import get_db_session, get_engine
from app.main import app
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.modules.employees import employee_service

CONCURRENCY = 40


@asynccontextmanager
async def _synchronized_race(monkeypatch, *, checked_function_name: str):
    """Гарантирует, что все `CONCURRENCY` сторон гонки:
    1. Физически могут держать по своему соединению одновременно
       (отдельный движок с пулом на весь `CONCURRENCY`, подставленный
       вместо `get_db_session`);
    2. Все прошли опережающую проверку `employee_service.
       <checked_function_name>` ДО того, как хоть одна из них продолжит
       к записи (барьер сразу после вызова настоящей функции).
    Ни то, ни другое не зависит от расписания планировщика или угаданной
    задержки."""
    large_pool_engine = create_async_engine(
        get_settings().database_url, pool_size=CONCURRENCY, max_overflow=0
    )
    large_pool_session_factory = async_sessionmaker(bind=large_pool_engine, expire_on_commit=False)

    async def _override_get_db_session():
        async with large_pool_session_factory() as session:
            yield session

    barrier = asyncio.Barrier(CONCURRENCY)
    original = getattr(employee_service, checked_function_name)

    async def _synced(*args, **kwargs):
        await original(*args, **kwargs)
        await barrier.wait()

    monkeypatch.setattr(employee_service, checked_function_name, _synced)
    app.dependency_overrides[get_db_session] = _override_get_db_session
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        await large_pool_engine.dispose()


async def _run_racing_group(coros: Iterable[Awaitable]) -> list:
    """Запускает все стороны гонки через общий барьер (`parties=len`):
    все гарантированно НАЧИНАЮТ выполнение в один и тот же тик event
    loop. Само по себе этого недостаточно для 40-way гонки на `UPDATE`
    (см. докстринг модуля) — отсюда `_synchronized_race` для более
    тонкой синхронизации там, где она нужна; здесь же барьер на старт
    остаётся, чтобы все 40 запросов были отправлены без искусственной
    задержки Python-цикла между ними."""
    coro_list = list(coros)
    barrier = asyncio.Barrier(len(coro_list))

    async def _released(coro: Awaitable):
        await barrier.wait()
        return await coro

    return await asyncio.gather(*(_released(c) for c in coro_list))


async def _make_hr_admin_token(marker: str) -> tuple[int, int]:
    engine = get_engine()
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        department = Department(name=f"Dup Race Dept {marker}")
        session.add(department)
        await session.flush()
        admin_employee = Employee(
            full_name="Dup Race Admin",
            personnel_number=f"DUPRACE-ADMIN-{marker}",
            department_id=department.id,
            position="HR",
            hire_date=datetime.date(2024, 1, 1),
            email=f"dup-race-admin-{marker}@company.local",
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
                        Employee.personnel_number == f"DUPRACE-ADMIN-{marker}",
                    )
                )
            )
        ).all()
        ids = [row.id for row in rows]
        await session.execute(Account.__table__.delete().where(Account.employee_id.in_(ids)))
        await session.execute(Employee.__table__.delete().where(Employee.id.in_(ids)))
        await session.execute(Department.__table__.delete().where(Department.id == department_id))
        await session.commit()
        await session.close()


async def test_concurrent_creates_with_colliding_email_never_500(client, monkeypatch) -> None:
    marker = uuid.uuid4().hex[:12]
    account_id, department_id = await _make_hr_admin_token(marker)
    token = create_access_token(account_id=account_id, role="hr_admin")
    headers = {"Authorization": f"Bearer {token}"}
    personnel_numbers = [f"DUPRACE-EMAIL-{marker}-{i}" for i in range(CONCURRENCY)]

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
                "email": (
                    f"{'DupRaceEmail' if i % 2 else 'dupraceemail'}-{marker}@Company.Local"
                ),
                "work_schedule_type": "standard",
            }
            for i in range(CONCURRENCY)
        ]

        async with _synchronized_race(monkeypatch, checked_function_name="_require_email_free"):
            responses = await _run_racing_group(
                client.post("/api/employees", json=p, headers=headers) for p in payloads
            )

        statuses = [r.status_code for r in responses]
        assert 500 not in statuses, statuses
        assert statuses.count(201) == 1, statuses
        assert statuses.count(400) == CONCURRENCY - 1, statuses
        for response in responses:
            if response.status_code == 400:
                assert "email" in response.json()["detail"]
    finally:
        await _cleanup(marker, department_id, personnel_numbers)


async def test_concurrent_creates_with_colliding_personnel_number_never_500(
    client, monkeypatch
) -> None:
    marker = uuid.uuid4().hex[:12]
    account_id, department_id = await _make_hr_admin_token(marker)
    token = create_access_token(account_id=account_id, role="hr_admin")
    headers = {"Authorization": f"Bearer {token}"}
    shared_personnel_number = f"DUPRACE-SHARED-NUMBER-{marker}"

    try:
        payloads = [
            {
                "full_name": f"Dup Race PN {i}",
                "personnel_number": shared_personnel_number,
                "department_id": department_id,
                "position": "Engineer",
                "hire_date": "2024-01-01",
                "email": f"dup-race-pn-{marker}-{i}@company.local",
                "work_schedule_type": "standard",
            }
            for i in range(CONCURRENCY)
        ]

        async with _synchronized_race(
            monkeypatch, checked_function_name="_require_personnel_number_free"
        ):
            responses = await _run_racing_group(
                client.post("/api/employees", json=p, headers=headers) for p in payloads
            )

        statuses = [r.status_code for r in responses]
        assert 500 not in statuses, statuses
        assert statuses.count(201) == 1, statuses
        assert statuses.count(400) == CONCURRENCY - 1, statuses
        for response in responses:
            if response.status_code == 400:
                assert "personnel_number" in response.json()["detail"]
    finally:
        await _cleanup(marker, department_id, [shared_personnel_number])


async def test_concurrent_updates_with_colliding_personnel_number_never_500(
    client, monkeypatch
) -> None:
    marker = uuid.uuid4().hex[:12]
    account_id, department_id = await _make_hr_admin_token(marker)
    token = create_access_token(account_id=account_id, role="hr_admin")
    headers = {"Authorization": f"Bearer {token}"}
    personnel_numbers = [f"DUPRACE-PUT-{marker}-{i}" for i in range(CONCURRENCY)]
    target_personnel_number = f"DUPRACE-PUT-TARGET-{marker}"

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
                email=f"dup-race-put-{marker}-{i}@company.local",
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
        async with _synchronized_race(
            monkeypatch, checked_function_name="_require_personnel_number_free"
        ):
            responses = await _run_racing_group(
                client.put(
                    f"/api/employees/{employee_ids[i]}",
                    json={
                        "full_name": f"Dup Race PUT {i}",
                        "personnel_number": target_personnel_number,
                        "department_id": department_id,
                        "position": "Engineer",
                        "hire_date": "2024-01-01",
                        "email": f"dup-race-put-{marker}-{i}@company.local",
                        "work_schedule_type": "standard",
                        "auth_type": "ldap",
                    },
                    headers=headers,
                )
                for i in range(CONCURRENCY)
            )

        statuses = [r.status_code for r in responses]
        assert 500 not in statuses, statuses
        assert statuses.count(200) == 1, statuses
        assert statuses.count(400) == CONCURRENCY - 1, statuses
        for response in responses:
            if response.status_code == 400:
                assert "personnel_number" in response.json()["detail"]
    finally:
        await _cleanup(marker, department_id, [*personnel_numbers, target_personnel_number])
