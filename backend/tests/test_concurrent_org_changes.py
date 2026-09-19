"""Гонки при конкурентных правках оргструктуры (PR review): два подлинно
одновременных `PUT`, назначающих сущности родителями/руководителями друг
друга, не должны оба пройти и оставить цикл в БД (write skew под READ
COMMITTED).

Эти тесты НЕ используют общую фикстуру `db_session` — та даёт одну
транзакцию (один SAVEPOINT) на тест, а гонка требует двух ПОДЛИННО
независимых соединений/транзакций, как у двух настоящих одновременных
HTTP-запросов. Тестовые строки создаются и удаляются вручную (реальный
`commit`, не откатывается автоматически по завершении теста); все
естественные ключи несут уникальный для прогона суффикс, а очистка в
`finally` ищет строки по этому суффиксу, а не по конкретным id — иначе
остаток от упавшего посередине прогона ломает не только повторный
запуск этого теста, но и соседние, которые считают строки в таблице.

Перекрытие сторон гонки обеспечивается барьером (`asyncio.Barrier`), а
не фиксированной задержкой: обе стороны начинают выполнение строго в
один и тот же тик event loop, поэтому дальнейшее чередование их
запросов к БД зависит от кооперативного планирования, а не от того,
успела ли одна сторона заснуть на заранее угаданное время. Фиксированная
задержка была ложноположительной защитой: если под нагрузкой вторая
сторона не успевала дойти до блокировки за отведённое время, первая
сторона завершалась и коммитила раньше, чем вторая вообще начинала
выполняться, — вторая сторона тогда просто читала уже применённое
изменение и корректно отказывала САМА ПО СЕБЕ, без всякого участия
блокировки. Тест в этом случае проходил, но не проверял ничего: тот же
результат дал бы полностью последовательный вызов без блокировки вовсе.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Awaitable
from contextlib import AsyncExitStack, asynccontextmanager

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import get_engine
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.modules.employees import department_service, employee_service
from app.schemas.department import DepartmentUpdate
from app.schemas.employee import EmployeeUpdate


@asynccontextmanager
async def _fresh_session(engine: AsyncEngine):
    """Сессия на СВОЁМ соединении из пула (не в SAVEPOINT общей
    фикстуры `db_session`) — обязательное условие для настоящей гонки
    между двумя независимыми транзакциями."""
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()


async def _run_racing_pair(coro_a: Awaitable, coro_b: Awaitable) -> list:
    """Запускает обе стороны гонки через общий барьер (`parties=2`): обе
    гарантированно НАЧИНАЮТ выполнение в один и тот же тик event loop —
    ни одна не может уйти вперёд и завершиться до того, как вторая
    вообще начала. Дальше это уже настоящая гонка двух независимых
    подключений к Postgres, а не гонка с угаданным таймаутом."""
    barrier = asyncio.Barrier(2)

    async def _released(coro: Awaitable):
        await barrier.wait()
        return await coro

    return await asyncio.gather(_released(coro_a), _released(coro_b), return_exceptions=True)


async def test_concurrent_department_parent_swap_rejects_one_and_avoids_cycle() -> None:
    engine = get_engine()
    marker = uuid.uuid4().hex[:12]
    dept_a_name = f"Race Dept A {marker}"
    dept_b_name = f"Race Dept B {marker}"

    async with _fresh_session(engine) as setup:
        dept_a = Department(name=dept_a_name)
        dept_b = Department(name=dept_b_name)
        setup.add_all([dept_a, dept_b])
        await setup.commit()
        dept_a_id, dept_b_id = dept_a.id, dept_b.id

    try:
        async with AsyncExitStack() as stack:
            session_1 = await stack.enter_async_context(_fresh_session(engine))
            session_2 = await stack.enter_async_context(_fresh_session(engine))

            payload_1 = DepartmentUpdate(name=dept_a_name, parent_id=dept_b_id)
            payload_2 = DepartmentUpdate(name=dept_b_name, parent_id=dept_a_id)

            results = await _run_racing_pair(
                department_service.update_department(session_1, dept_a_id, payload_1),
                department_service.update_department(session_2, dept_b_id, payload_2),
            )

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 1, f"expected exactly one success, got {results!r}"
        assert len(failures) == 1, f"expected exactly one rejection, got {results!r}"
        assert isinstance(failures[0], ValueError), failures[0]

        async with _fresh_session(engine) as verify:
            dept_a_row = await verify.get(Department, dept_a_id)
            dept_b_row = await verify.get(Department, dept_b_id)
        # Цикла быть не должно: не могут оба указывать друг на друга.
        assert not (dept_a_row.parent_id == dept_b_id and dept_b_row.parent_id == dept_a_id)
    finally:
        async with _fresh_session(engine) as cleanup:
            await cleanup.execute(
                delete(Department).where(
                    or_(Department.name == dept_a_name, Department.name == dept_b_name)
                )
            )
            await cleanup.commit()


async def test_concurrent_employee_manager_swap_rejects_one_and_avoids_cycle() -> None:
    engine = get_engine()
    marker = uuid.uuid4().hex[:12]
    department_name = f"Race Employees Dept {marker}"
    personnel_number_a = f"RACE-A-{marker}"
    personnel_number_b = f"RACE-B-{marker}"
    email_a = f"race-employee-a-{marker}@company.local"
    email_b = f"race-employee-b-{marker}@company.local"

    async with _fresh_session(engine) as setup:
        department = Department(name=department_name)
        setup.add(department)
        await setup.flush()
        employee_a = Employee(
            full_name="Race Employee A",
            personnel_number=personnel_number_a,
            department_id=department.id,
            position="Engineer",
            hire_date=datetime.date(2024, 1, 1),
            email=email_a,
            work_schedule_type="standard",
        )
        employee_b = Employee(
            full_name="Race Employee B",
            personnel_number=personnel_number_b,
            department_id=department.id,
            position="Engineer",
            hire_date=datetime.date(2024, 1, 1),
            email=email_b,
            work_schedule_type="standard",
        )
        setup.add_all([employee_a, employee_b])
        await setup.flush()
        setup.add_all(
            [
                Account(employee_id=employee_a.id, auth_type="ldap", role="employee"),
                Account(employee_id=employee_b.id, auth_type="ldap", role="employee"),
            ]
        )
        await setup.commit()
        employee_a_id, employee_b_id = employee_a.id, employee_b.id
        department_id = department.id

    try:
        async with AsyncExitStack() as stack:
            session_1 = await stack.enter_async_context(_fresh_session(engine))
            session_2 = await stack.enter_async_context(_fresh_session(engine))

            payload_1 = EmployeeUpdate(
                full_name="Race Employee A",
                personnel_number=personnel_number_a,
                department_id=department_id,
                position="Engineer",
                hire_date="2024-01-01",
                email=email_a,
                work_schedule_type="standard",
                auth_type="ldap",
                manager_id=employee_b_id,
            )
            payload_2 = EmployeeUpdate(
                full_name="Race Employee B",
                personnel_number=personnel_number_b,
                department_id=department_id,
                position="Engineer",
                hire_date="2024-01-01",
                email=email_b,
                work_schedule_type="standard",
                auth_type="ldap",
                manager_id=employee_a_id,
            )

            results = await _run_racing_pair(
                employee_service.update_employee(session_1, employee_a_id, payload_1),
                employee_service.update_employee(session_2, employee_b_id, payload_2),
            )

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 1, f"expected exactly one success, got {results!r}"
        assert len(failures) == 1, f"expected exactly one rejection, got {results!r}"
        assert isinstance(failures[0], ValueError), failures[0]

        async with _fresh_session(engine) as verify:
            employee_a_row = await verify.get(Employee, employee_a_id)
            employee_b_row = await verify.get(Employee, employee_b_id)
        assert not (
            employee_a_row.manager_id == employee_b_id
            and employee_b_row.manager_id == employee_a_id
        )
    finally:
        async with _fresh_session(engine) as cleanup:
            await cleanup.execute(
                delete(Account).where(Account.employee_id.in_([employee_a_id, employee_b_id]))
            )
            await cleanup.execute(
                delete(Employee).where(
                    or_(
                        Employee.personnel_number == personnel_number_a,
                        Employee.personnel_number == personnel_number_b,
                    )
                )
            )
            await cleanup.execute(delete(Department).where(Department.name == department_name))
            await cleanup.commit()
