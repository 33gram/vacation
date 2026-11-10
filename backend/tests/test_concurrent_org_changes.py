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

from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import get_engine
from app.models.account import Account
from app.models.department import Department
from app.models.employee import Employee
from app.models.leave_balance_entry import ACCRUAL, RECALCULATION, LeaveBalanceEntry
from app.modules.balances.accrual import accrual_steps
from app.modules.balances.service import accrue_before_read
from app.modules.employees import department_service, employee_service
from app.schemas.department import DepartmentUpdate
from app.schemas.employee import EmployeeDeactivate, EmployeeUpdate

# `update_employee` требует автора только для пересчёта остатка при смене даты
# приёма (T-M2-07); эти тесты дату не меняют, автор не используется.
_UNUSED_ACTOR = 0


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
                employee_service.update_employee(
                    session_1, employee_a_id, payload_1, actor_account_id=_UNUSED_ACTOR
                ),
                employee_service.update_employee(
                    session_2, employee_b_id, payload_2, actor_account_id=_UNUSED_ACTOR
                ),
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


async def test_concurrent_deactivate_and_assign_subordinate_never_leaves_bad_state() -> None:
    """PR #139 review, verification item 4: the review flagged this as
    unchecked — same write skew as the manager-swap race above, on a
    different pair of paths (`deactivate_employee` vs. `update_employee`
    assigning a subordinate). Concurrently: deactivate manager A, and
    assign employee B to report to A. Neither ordering may leave A
    terminated with B still reporting to them — exactly one of the two
    operations must succeed, whichever loses the race must see the
    other's already-committed state and refuse cleanly."""
    engine = get_engine()
    marker = uuid.uuid4().hex[:12]
    department_name = f"Race Deactivate Dept {marker}"
    personnel_number_a = f"RACE-DEACT-A-{marker}"
    personnel_number_b = f"RACE-DEACT-B-{marker}"
    email_a = f"race-deact-a-{marker}@company.local"
    email_b = f"race-deact-b-{marker}@company.local"

    async with _fresh_session(engine) as setup:
        department = Department(name=department_name)
        setup.add(department)
        await setup.flush()
        employee_a = Employee(
            full_name="Race Deactivate A",
            personnel_number=personnel_number_a,
            department_id=department.id,
            position="Engineer",
            hire_date=datetime.date(2024, 1, 1),
            email=email_a,
            work_schedule_type="standard",
        )
        employee_b = Employee(
            full_name="Race Deactivate B",
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

            deactivate_payload = EmployeeDeactivate(termination_date=datetime.date(2024, 6, 1))
            assign_payload = EmployeeUpdate(
                full_name="Race Deactivate B",
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
                employee_service.deactivate_employee(session_1, employee_a_id, deactivate_payload),
                employee_service.update_employee(
                    session_2, employee_b_id, assign_payload, actor_account_id=_UNUSED_ACTOR
                ),
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
            employee_a_row.employment_status == "terminated"
            and employee_b_row.manager_id == employee_a_id
            and employee_b_row.employment_status == "active"
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


async def test_concurrent_deactivate_and_assign_department_head_never_leaves_bad_state(
    monkeypatch,
) -> None:
    """PR #139 review, замечание B: та же гонка как между деактивацией и
    назначением подчинённого (`test_concurrent_deactivate_and_assign_
    subordinate_never_leaves_bad_state`), только на паре путей
    `deactivate_employee` / `department_service.update_department`
    (`head_employee_id`), закрытых общим `lock_management_chain`.

    Барьер только на СТАРТ (`_run_racing_pair`) здесь недостаточен: у
    `update_department` до захвата блокировки меньше промежуточных
    `await` (одно чтение подразделения), чем у `deactivate_employee`
    (чтение сотрудника плюс чтение учётной записи), и при простом
    старте в один тик более короткий путь надёжно доходит до блокировки
    и коммитит первым — блокировка тогда ни разу не встречает
    конкурента, а вторая сторона своей проверкой видит уже применённое
    изменение и отказывает САМА ПО СЕБЕ (проверено прогоном: с барьером
    только на старт тест зелёный даже при закомментированной блокировке
    — тот же ложноположительный сценарий, который описан в докстринге
    модуля выше, только обнаруженный не рассуждением, а прогоном —
    правило 8).

    Барьер здесь — ПЕРЕД захватом блокировки (после обязательного
    предварительного чтения каждой стороны, `_require_department`/
    `get_account_by_employee_id`), а не после проверки, которая идёт
    ПОСЛЕ блокировки: под настоящей блокировкой до этой более поздней
    точки одновременно доходит только ОДНА сторона (в этом и есть
    смысл блокировки), поэтому барьер там ждал бы вторую сторону,
    которая в это время сама ждёт снятия блокировки, — взаимная
    блокировка (deadlock). Синхронизация ДО захвата уравнивает разницу в
    длине пути, не мешая последующей настоящей гонке за блокировку в
    Postgres."""
    barrier = asyncio.Barrier(2)
    original_department_check = department_service._require_department
    original_account_lookup = employee_service.get_account_by_employee_id

    async def _synced_department_check(*args: object, **kwargs: object):
        result = await original_department_check(*args, **kwargs)
        await barrier.wait()
        return result

    async def _synced_account_lookup(*args: object, **kwargs: object):
        result = await original_account_lookup(*args, **kwargs)
        await barrier.wait()
        return result

    monkeypatch.setattr(department_service, "_require_department", _synced_department_check)
    monkeypatch.setattr(employee_service, "get_account_by_employee_id", _synced_account_lookup)

    engine = get_engine()
    marker = uuid.uuid4().hex[:12]
    department_name = f"Race Head Dept {marker}"
    personnel_number_a = f"RACE-HEAD-A-{marker}"
    email_a = f"race-head-a-{marker}@company.local"

    async with _fresh_session(engine) as setup:
        department = Department(name=department_name)
        setup.add(department)
        await setup.flush()
        employee_a = Employee(
            full_name="Race Head A",
            personnel_number=personnel_number_a,
            department_id=department.id,
            position="Engineer",
            hire_date=datetime.date(2024, 1, 1),
            email=email_a,
            work_schedule_type="standard",
        )
        setup.add(employee_a)
        await setup.flush()
        setup.add(Account(employee_id=employee_a.id, auth_type="ldap", role="employee"))
        await setup.commit()
        employee_a_id = employee_a.id
        department_id = department.id

    try:
        async with AsyncExitStack() as stack:
            session_1 = await stack.enter_async_context(_fresh_session(engine))
            session_2 = await stack.enter_async_context(_fresh_session(engine))

            deactivate_payload = EmployeeDeactivate(termination_date=datetime.date(2024, 6, 1))
            assign_head_payload = DepartmentUpdate(
                name=department_name, head_employee_id=employee_a_id
            )

            results = await _run_racing_pair(
                employee_service.deactivate_employee(session_1, employee_a_id, deactivate_payload),
                department_service.update_department(
                    session_2, department_id, assign_head_payload
                ),
            )

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 1, f"expected exactly one success, got {results!r}"
        assert len(failures) == 1, f"expected exactly one rejection, got {results!r}"
        assert isinstance(failures[0], ValueError), failures[0]

        async with _fresh_session(engine) as verify:
            employee_a_row = await verify.get(Employee, employee_a_id)
            department_row = await verify.get(Department, department_id)
        assert not (
            employee_a_row.employment_status == "terminated"
            and department_row.head_employee_id == employee_a_id
        )
    finally:
        async with _fresh_session(engine) as cleanup:
            await cleanup.execute(delete(Account).where(Account.employee_id == employee_a_id))
            # Взаимная ссылка (employees.department_id -> departments.id
            # И, если гонку выиграло назначение руководителя, departments.
            # head_employee_id -> employees.id) — ни одну из двух строк
            # нельзя удалить первой без ошибки внешнего ключа. Разрываем
            # ссылку подразделения на сотрудника явно, потом обе строки
            # можно удалять в любом порядке.
            await cleanup.execute(
                update(Department)
                .where(Department.name == department_name)
                .values(head_employee_id=None)
            )
            await cleanup.execute(
                delete(Employee).where(Employee.personnel_number == personnel_number_a)
            )
            await cleanup.execute(delete(Department).where(Department.name == department_name))
            await cleanup.commit()


async def test_concurrent_hire_date_edits_recalculate_from_the_committed_previous_date() -> None:
    """Две одновременные правки даты приёма одного сотрудника (T-M2-07): «было»
    читается под замком строки, поэтому вторая правка пересчитывает от даты,
    зафиксированной первой, а не от устаревшей. Без `refresh` под замком причина
    второго пересчёта назвала бы исходную дату вместо зафиксированной первой.

    Что НЕ доказано: отсутствие взаимной блокировки - вывод из порядка замков
    (цепочка -> строка) по чтению кода и правилам блокировок PostgreSQL, этот
    тест путь с цепочкой подчинения не гоняет."""
    engine = get_engine()
    marker = uuid.uuid4().hex[:12]
    today = datetime.datetime.now(datetime.UTC).date()
    original = today - datetime.timedelta(days=200)
    date_a = original + datetime.timedelta(days=10)
    date_b = original + datetime.timedelta(days=20)
    department_name = f"Race Hire Dept {marker}"
    personnel_number = f"RACE-H-{marker}"
    email = f"race-hire-{marker}@company.local"

    def payload(hire_date: datetime.date, department_id: int) -> EmployeeUpdate:
        return EmployeeUpdate(
            full_name="Race Hire",
            personnel_number=personnel_number,
            department_id=department_id,
            position="Engineer",
            hire_date=hire_date,
            email=email,
            work_schedule_type="standard",
            auth_type="ldap",
        )

    async with _fresh_session(engine) as setup:
        department = Department(name=department_name)
        setup.add(department)
        await setup.flush()
        employee = Employee(
            full_name="Race Hire",
            personnel_number=personnel_number,
            department_id=department.id,
            position="Engineer",
            hire_date=original,
            email=email,
            work_schedule_type="standard",
        )
        setup.add(employee)
        await setup.flush()
        account = Account(employee_id=employee.id, auth_type="ldap", role="employee")
        setup.add(account)
        await setup.flush()
        leave_type_id = (
            await setup.execute(text("SELECT id FROM leave_types WHERE code = 'annual_paid'"))
        ).scalar_one()
        setup.add(
            LeaveBalanceEntry(
                employee_id=employee.id,
                leave_type_id=leave_type_id,
                working_year_start=original,
                working_year_end=original.replace(year=original.year + 1),
                entry_type=ACCRUAL,
                quantity=15,
                entry_date=today,
            )
        )
        await setup.commit()
        employee_id, account_id, department_id = employee.id, account.id, department.id
    try:
        async with AsyncExitStack() as stack:
            session_1 = await stack.enter_async_context(_fresh_session(engine))
            session_2 = await stack.enter_async_context(_fresh_session(engine))
            results = await _run_racing_pair(
                employee_service.update_employee(
                    session_1,
                    employee_id,
                    payload(date_a, department_id),
                    actor_account_id=account_id,
                ),
                employee_service.update_employee(
                    session_2,
                    employee_id,
                    payload(date_b, department_id),
                    actor_account_id=account_id,
                ),
            )
        assert all(not isinstance(r, Exception) for r in results), results

        async with _fresh_session(engine) as verify:
            final = (await verify.get(Employee, employee_id)).hire_date
            reasons = [
                e.reason
                for e in (
                    await verify.execute(
                        select(LeaveBalanceEntry).where(
                            LeaveBalanceEntry.employee_id == employee_id,
                            LeaveBalanceEntry.entry_type == RECALCULATION,
                        )
                    )
                ).scalars()
            ]
        first = date_a if final == date_b else date_b
        assert final in (date_a, date_b)
        # Изменения сериализованы: второй пересчёт называет «было» = дату первой
        # правки, и НИ ОДИН пересчёт не называет переход с исходной даты на
        # итоговую (так выглядела бы устаревшая «было»).
        stale = f"с {original:%d.%m.%Y} на {final:%d.%m.%Y}"
        assert not any(stale in reason for reason in reasons), reasons
        expected = f"с {first:%d.%m.%Y} на {final:%d.%m.%Y}"
        assert any(expected in reason for reason in reasons), reasons
    finally:
        async with _fresh_session(engine) as cleanup:
            await cleanup.execute(
                text(
                    "DELETE FROM leave_balance_entries WHERE employee_id IN "
                    "(SELECT id FROM employees WHERE personnel_number = :pn)"
                ),
                {"pn": personnel_number},
            )
            await cleanup.execute(
                text(
                    "DELETE FROM accounts WHERE employee_id IN "
                    "(SELECT id FROM employees WHERE personnel_number = :pn)"
                ),
                {"pn": personnel_number},
            )
            await cleanup.execute(
                delete(Employee).where(Employee.personnel_number == personnel_number)
            )
            await cleanup.execute(delete(Department).where(Department.name == department_name))
            await cleanup.commit()


# --- модуль balances: замок строки сотрудника и перечитывание (T-M2-11) -------------


@asynccontextmanager
async def _committed_employee(engine: AsyncEngine, hire_date: datetime.date):
    """Сотрудник, реально зафиксированный в БД (для гонок между сессиями); по выходу
    его события и строка удаляются по уникальному табельному номеру."""
    marker = uuid.uuid4().hex[:12]
    personnel_number = f"RACE-B-{marker}"
    async with _fresh_session(engine) as setup:
        employee = Employee(
            full_name="Race Balance",
            personnel_number=personnel_number,
            position="Engineer",
            hire_date=hire_date,
            email=f"race-balance-{marker}@company.local",
            work_schedule_type="standard",
        )
        setup.add(employee)
        await setup.commit()
        employee_id = employee.id
    try:
        yield employee_id
    finally:
        async with _fresh_session(engine) as cleanup:
            await cleanup.execute(
                text("DELETE FROM leave_balance_entries WHERE employee_id = :id"),
                {"id": employee_id},
            )
            await cleanup.execute(delete(Employee).where(Employee.id == employee_id))
            await cleanup.commit()


async def _change_hire_date(engine: AsyncEngine, employee_id: int, hire_date: datetime.date):
    async with _fresh_session(engine) as other:
        await other.execute(
            update(Employee).where(Employee.id == employee_id).values(hire_date=hire_date)
        )
        await other.commit()


async def test_for_update_alone_does_not_refresh_a_loaded_employee_populate_existing_does() -> None:
    """Закрепляет то, на чём стоит `_lock_employee` (измерено на sqlalchemy 2.0.x):
    `get(..., with_for_update=True)` для уже загруженного объекта атрибуты НЕ
    обновляет, `populate_existing=True` - обновляет."""
    engine = get_engine()
    today = datetime.datetime.now(datetime.UTC).date()
    old, new = today - datetime.timedelta(days=200), today - datetime.timedelta(days=100)
    async with _committed_employee(engine, old) as employee_id:
        async with _fresh_session(engine) as session:
            loaded = await session.get(Employee, employee_id)
            await _change_hire_date(engine, employee_id, new)

            stale = await session.get(Employee, employee_id, with_for_update=True)
            assert stale is loaded and stale.hire_date == old  # замок взят, дата устарела
            await session.rollback()  # снять замок
            fresh = await session.get(
                Employee, employee_id, with_for_update=True, populate_existing=True
            )
            assert fresh.hire_date == new


async def test_accrual_uses_the_hire_date_committed_by_another_transaction() -> None:
    """Слой API читает сотрудника раньше начисления; параллельная транзакция меняет
    дату приёма. Начисление должно считаться по НОВОЙ дате (замок + перечитывание)."""
    engine = get_engine()
    today = datetime.datetime.now(datetime.UTC).date()
    old, new = today - datetime.timedelta(days=200), today - datetime.timedelta(days=100)
    async with _committed_employee(engine, old) as employee_id:
        async with _fresh_session(engine) as session:
            # Предзагрузка, как в роутере. Ссылку НАДО держать: identity map слабая, и
            # без ссылки объект был бы загружен заново, а тест ничего не проверял бы.
            preloaded = await session.get(Employee, employee_id)
            await _change_hire_date(engine, employee_id, new)

            await accrue_before_read(session, employee_id=employee_id, as_of=today)
            await session.commit()
            assert preloaded.hire_date == new  # карточка в сессии перечитана

        async with _fresh_session(engine) as verify:
            years = (
                await verify.execute(
                    select(LeaveBalanceEntry.working_year_start).where(
                        LeaveBalanceEntry.employee_id == employee_id
                    )
                )
            ).scalars().all()
        assert set(years) == {new}  # а не {old}: расчёт по устаревшей карточке


async def test_two_concurrent_reads_write_the_accrual_once() -> None:
    """Два одновременных ленивых начисления по одному сотруднику сериализуются на замке
    строки: событие начисления одно, а не два. Раньше это было рассуждением, а не
    проверенным сценарием."""
    engine = get_engine()
    today = datetime.datetime.now(datetime.UTC).date()
    async with _committed_employee(engine, today - datetime.timedelta(days=200)) as employee_id:

        async def read(session: AsyncSession) -> None:
            await accrue_before_read(session, employee_id=employee_id, as_of=today)
            await session.commit()

        async with AsyncExitStack() as stack:
            session_1 = await stack.enter_async_context(_fresh_session(engine))
            session_2 = await stack.enter_async_context(_fresh_session(engine))
            results = await _run_racing_pair(read(session_1), read(session_2))
        assert all(not isinstance(r, Exception) for r in results), results

        async with _fresh_session(engine) as verify:
            events = (
                await verify.execute(
                    select(LeaveBalanceEntry).where(LeaveBalanceEntry.employee_id == employee_id)
                )
            ).scalars().all()
        # По событию на шаг (T-M2-21): каждый шаг записан один раз, а не по разу от каждой сессии.
        hire = today - datetime.timedelta(days=200)
        expected = [
            step.date
            for step in accrual_steps(
                hire_date=hire,
                working_year_start=hire,
                as_of=today,
                annual_norm=28,
                rounding_threshold_days=15,
                excluded_days=0,
            )
        ]
        assert sorted(e.entry_date for e in events) == expected
        assert {e.entry_type for e in events} == {ACCRUAL}
