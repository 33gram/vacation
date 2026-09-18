"""Гонки при конкурентных правках оргструктуры (PR review): два подлинно
одновременных `PUT`, назначающих сущности родителями/руководителями друг
друга, не должны оба пройти и оставить цикл в БД (write skew под READ
COMMITTED).

Эти тесты НЕ используют общую фикстуру `db_session` — та даёт одну
транзакцию (один SAVEPOINT) на тест, а гонка требует двух ПОДЛИННО
независимых соединений/транзакций, как у двух настоящих одновременных
HTTP-запросов. Тестовые строки создаются и удаляются вручную (реальный
`commit`, не откатывается автоматически по завершении теста).

Чистое `asyncio.gather` не гарантирует, что обе транзакции успеют
прочитать состояние ДО того, как одна из них запишет своё изменение —
на быстром локальном Postgres обе стороны гонки иногда успевают
выполниться настолько быстро, что фактически не пересекаются по
времени, и тест был бы нестабильным (то красный, то зелёный на одном и
том же коде). Поэтому обе гонки принудительно раздвигаются монки-
патчем: сторона A выполняет проверку цикла, затем ждёт, отдавая
управление стороне B, — гарантированное перекрытие вместо везения
планировщика.
"""

from __future__ import annotations

import asyncio
import datetime
from contextlib import AsyncExitStack, asynccontextmanager

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import get_engine
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.modules.employees import department_service, employee_service
from app.schemas.department import DepartmentUpdate
from app.schemas.employee import EmployeeUpdate

_OVERLAP_DELAY_SECONDS = 0.2


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


async def test_concurrent_department_parent_swap_rejects_one_and_avoids_cycle(
    monkeypatch,
) -> None:
    engine = get_engine()

    async with _fresh_session(engine) as setup:
        dept_a = Department(name="Race Dept A")
        dept_b = Department(name="Race Dept B")
        setup.add_all([dept_a, dept_b])
        await setup.commit()
        dept_a_id, dept_b_id = dept_a.id, dept_b.id

    try:
        original_would_create_cycle = department_service._would_create_cycle

        async def _delayed_would_create_cycle(session, department_id, new_parent_id):
            result = await original_would_create_cycle(session, department_id, new_parent_id)
            if department_id == dept_a_id:
                # Сторона A "читает и задумывается" — за это время сторона
                # B должна успеть полностью прочитать и записать своё
                # ребро (без блокировки), либо доехать до блокировки и
                # встать в очередь на неё (с блокировкой).
                await asyncio.sleep(_OVERLAP_DELAY_SECONDS)
            return result

        monkeypatch.setattr(
            department_service, "_would_create_cycle", _delayed_would_create_cycle
        )

        async with AsyncExitStack() as stack:
            session_1 = await stack.enter_async_context(_fresh_session(engine))
            session_2 = await stack.enter_async_context(_fresh_session(engine))

            payload_1 = DepartmentUpdate(name="Race Dept A", parent_id=dept_b_id)
            payload_2 = DepartmentUpdate(name="Race Dept B", parent_id=dept_a_id)

            results = await asyncio.gather(
                department_service.update_department(session_1, dept_a_id, payload_1),
                department_service.update_department(session_2, dept_b_id, payload_2),
                return_exceptions=True,
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
                delete(Department).where(Department.id.in_([dept_a_id, dept_b_id]))
            )
            await cleanup.commit()


async def test_concurrent_employee_manager_swap_rejects_one_and_avoids_cycle(
    monkeypatch,
) -> None:
    engine = get_engine()

    async with _fresh_session(engine) as setup:
        department = Department(name="Race Employees Dept")
        setup.add(department)
        await setup.flush()
        employee_a = Employee(
            full_name="Race Employee A",
            personnel_number="RACE-A",
            department_id=department.id,
            position="Engineer",
            hire_date=datetime.date(2024, 1, 1),
            email="race-employee-a@company.local",
            work_schedule_type="standard",
        )
        employee_b = Employee(
            full_name="Race Employee B",
            personnel_number="RACE-B",
            department_id=department.id,
            position="Engineer",
            hire_date=datetime.date(2024, 1, 1),
            email="race-employee-b@company.local",
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
        original_is_in_management_chain = employee_service.is_in_management_chain

        async def _delayed_is_in_management_chain(session, manager_employee_id, target_employee_id):
            result = await original_is_in_management_chain(
                session, manager_employee_id, target_employee_id
            )
            if target_employee_id == employee_a_id:
                await asyncio.sleep(_OVERLAP_DELAY_SECONDS)
            return result

        monkeypatch.setattr(
            employee_service, "is_in_management_chain", _delayed_is_in_management_chain
        )

        async with AsyncExitStack() as stack:
            session_1 = await stack.enter_async_context(_fresh_session(engine))
            session_2 = await stack.enter_async_context(_fresh_session(engine))

            payload_1 = EmployeeUpdate(
                full_name="Race Employee A",
                personnel_number="RACE-A",
                department_id=department_id,
                position="Engineer",
                hire_date="2024-01-01",
                email="race-employee-a@company.local",
                work_schedule_type="standard",
                auth_type="ldap",
                manager_id=employee_b_id,
            )
            payload_2 = EmployeeUpdate(
                full_name="Race Employee B",
                personnel_number="RACE-B",
                department_id=department_id,
                position="Engineer",
                hire_date="2024-01-01",
                email="race-employee-b@company.local",
                work_schedule_type="standard",
                auth_type="ldap",
                manager_id=employee_a_id,
            )

            results = await asyncio.gather(
                employee_service.update_employee(session_1, employee_a_id, payload_1),
                employee_service.update_employee(session_2, employee_b_id, payload_2),
                return_exceptions=True,
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
                delete(Employee).where(Employee.id.in_([employee_a_id, employee_b_id]))
            )
            await cleanup.execute(delete(Department).where(Department.id == department_id))
            await cleanup.commit()
