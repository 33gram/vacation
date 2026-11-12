"""Проводка `FR-BAL-003` в `PUT /api/employees/{id}` (T-M2-07): `WHEN` наступает в
момент правки карточки, а не в момент существования функции пересчёта.

Даты считаются от сегодняшней, потому что `update_employee` берёт «сегодня»
как дату расчёта: тест не должен зависеть от того, какой сегодня день.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select

from app.core.time_utils import utcnow
from app.models.employee import Employee
from app.models.leave_balance_entry import ACCRUAL, RECALCULATION, LeaveBalanceEntry
from app.modules.balances.service import accrue_leave, adjust_balance
from app.modules.employees import employee_service
from tests.modules.auth.factories import (
    bearer_header,
    make_account,
    make_department,
    make_employee,
)
from tests.modules.balances.helpers import make_context

TODAY = utcnow().date()  # тот же источник, что в update_employee
HIRED = TODAY - datetime.timedelta(days=200)


async def _setup(db_session):
    """Сотрудник с начислением за текущий рабочий год, аккаунт HR-администратора."""
    ctx = await make_context(db_session, "wire")
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = HIRED
    await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    await db_session.flush()
    admin_employee = await make_employee(db_session, email="wire-admin@company.local")
    admin = await make_account(
        db_session, employee_id=admin_employee.id, auth_type="local", role="hr_admin"
    )
    department = await make_department(db_session)
    return ctx, employee, admin, department


def _payload(employee: Employee, department_id: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "full_name": employee.full_name,
        "personnel_number": employee.personnel_number,
        "department_id": department_id,
        "position": employee.position,
        "hire_date": employee.hire_date.isoformat(),
        "email": employee.email,
        "work_schedule_type": employee.work_schedule_type,
        "auth_type": "ldap",
    }
    payload.update(overrides)
    return payload


async def _events(db_session, employee_id: int) -> list[LeaveBalanceEntry]:
    result = await db_session.execute(
        select(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == employee_id)
        .order_by(LeaveBalanceEntry.id)
    )
    return list(result.scalars())


async def _accrue_now(db_session, ctx) -> None:
    await accrue_leave(
        db_session, employee_id=ctx.employee_id, leave_type_id=ctx.leave_type_id, as_of=TODAY
    )


async def test_fr_bal_003_changing_the_hire_date_recalculates_in_the_same_request(
    db_session, api_client
) -> None:
    ctx, employee, admin, department = await _setup(db_session)
    await _accrue_now(db_session, ctx)
    old_steps = [e for e in await _events(db_session, employee.id) if e.entry_type == ACCRUAL]
    new_hire = HIRED + datetime.timedelta(days=10)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_payload(employee, department.id, hire_date=new_hire.isoformat()),
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    recalculations = [
        e for e in await _events(db_session, employee.id) if e.entry_type == RECALCULATION
    ]
    # T-M2-32: закрытие - зеркало на каждый прежний шаг начисления.
    assert len(recalculations) == len(old_steps) > 1
    for event in recalculations:
        assert event.quantity < 0
        assert event.actor_account_id == admin.id  # аутентифицированный аккаунт
        assert HIRED.strftime("%d.%m.%Y") in event.reason
        assert new_hire.strftime("%d.%m.%Y") in event.reason


async def test_fr_bal_003_changing_a_field_that_does_not_affect_the_norm_recalculates_nothing(
    db_session, api_client
) -> None:
    ctx, employee, admin, department = await _setup(db_session)
    await _accrue_now(db_session, ctx)
    before = len(await _events(db_session, employee.id))

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_payload(employee, department.id, position="Старший инженер"),
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    assert len(await _events(db_session, employee.id)) == before


async def test_fr_bal_003_changing_the_leave_category_writes_no_event_today(
    db_session, api_client
) -> None:
    # Категория названа в критерии, но норма от неё не зависит (A-3.1): разность ноль,
    # событий нет. Случай НЕ обработан пересчётом, а оставлен вне его намеренно.
    ctx, employee, admin, department = await _setup(db_session)
    await _accrue_now(db_session, ctx)
    before = len(await _events(db_session, employee.id))

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_payload(employee, department.id, leave_category="extended"),
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    assert len(await _events(db_session, employee.id)) == before


async def test_fr_bal_003_an_employee_without_accrual_events_gets_nothing_written(
    db_session, api_client
) -> None:
    ctx, employee, admin, department = await _setup(db_session)
    new_hire = HIRED + datetime.timedelta(days=10)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_payload(employee, department.id, hire_date=new_hire.isoformat()),
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    assert await _events(db_session, employee.id) == []


async def test_fr_bal_003_a_refusal_of_the_recalculation_is_a_409_and_writes_no_event(
    db_session, api_client, monkeypatch
) -> None:
    ctx, employee, admin, department = await _setup(db_session)
    await _accrue_now(db_session, ctx)
    await adjust_balance(
        db_session,
        employee_id=employee.id,
        leave_type_id=ctx.leave_type_id,
        working_year_start=HIRED,
        working_year_end=HIRED.replace(year=HIRED.year + 1),
        quantity=3,
        reason="Перенос",
        actor_account_id=admin.id,
        entry_date=TODAY,
    )
    before = len(await _events(db_session, employee.id))
    commits = _count_commits(db_session, monkeypatch)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_payload(
            employee,
            department.id,
            hire_date=(HIRED + datetime.timedelta(days=10)).isoformat(),
        ),
        headers=bearer_header(admin),
    )

    # Конфликт с уже записанными событиями - 409 (правило кодов отказа, `CLAUDE.md`).
    assert response.status_code == 409
    assert "manual_adjustment" in response.json()["detail"]
    assert len(await _events(db_session, employee.id)) == before
    # Узкое утверждение, которое тест МОЖЕТ проверить: `commit` не вызывался. Что
    # правка карточки не сохраняется, обеспечивает закрытие сессии без фиксации
    # в `get_db_session`; здесь сессия общая (`api_client` подменяет её на
    # `db_session`), и в памяти новая дата уже стоит, поэтому откат в БД тестом
    # этой обвязки не показать.
    assert commits == []


async def test_fr_bal_003_a_successful_edit_does_commit(
    db_session, api_client, monkeypatch
) -> None:
    # Положительный контроль к тесту выше: перехват `commit` видит фиксацию.
    ctx, employee, admin, department = await _setup(db_session)
    commits = _count_commits(db_session, monkeypatch)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_payload(employee, department.id, position="Старший инженер"),
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    assert len(commits) == 1


def _count_commits(db_session, monkeypatch) -> list[int]:
    commits: list[int] = []
    original = db_session.commit

    async def spy() -> None:
        commits.append(1)
        await original()

    monkeypatch.setattr(db_session, "commit", spy)
    return commits


async def test_fr_bal_003_a_field_added_to_the_set_triggers_the_recalculation(
    db_session, api_client, monkeypatch
) -> None:
    # Набор BALANCE_RECALCULATION_FIELDS УПРАВЛЯЕТ условием: добавленное в него поле
    # вызывает пересчёт. Без этого теста перечень выглядел бы управляющим и не был им.
    ctx, employee, admin, department = await _setup(db_session)
    calls: list[dict] = []

    async def spy(session, **kwargs):
        calls.append(kwargs["changes"])

    monkeypatch.setattr(employee_service, "recalculate_balance", spy)
    monkeypatch.setattr(
        employee_service, "BALANCE_RECALCULATION_FIELDS", ("hire_date", "leave_category")
    )

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_payload(employee, department.id, leave_category="extended"),
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    assert calls == [{"leave_category": (None, "extended")}]


async def test_fr_bal_003_a_field_outside_the_set_does_not_call_the_recalculation(
    db_session, api_client, monkeypatch
) -> None:
    ctx, employee, admin, department = await _setup(db_session)
    calls: list[dict] = []

    async def spy(session, **kwargs):
        calls.append(kwargs["changes"])

    monkeypatch.setattr(employee_service, "recalculate_balance", spy)

    response = await api_client.put(
        f"/api/employees/{employee.id}",
        json=_payload(employee, department.id, leave_category="extended"),
        headers=bearer_header(admin),
    )

    assert response.status_code == 200
    assert calls == []
