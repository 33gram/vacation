"""Эндпоинты остатка (T-M2-11, `FR-BAL-002`, `FR-BAL-004`) вместе с ленивым начислением
при чтении (T-M2-19).

Даты считаются от сегодняшней (UTC): эндпоинт берёт «сегодня» из единого источника
времени. Точные суммы начисления проверены в `test_accrual_*`; здесь проверяется то,
что делает слой API: доступ, состав ответа, ленивое начисление, корректировка.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.core.time_utils import utcnow
from app.models.employee import Employee
from app.models.leave_balance_entry import (
    ACCRUAL,
    INITIAL_IMPORT,
    MANUAL_ADJUSTMENT,
    LeaveBalanceEntry,
)
from app.modules.balances.service import LeaveTypeNotSeededError
from tests.modules.auth.factories import bearer_header, make_account, make_employee
from tests.modules.balances.helpers import make_context

TODAY = utcnow().date()
HIRED = TODAY - datetime.timedelta(days=200)


async def _setup(db_session):
    """Сотрудник с учётной записью, принятый 200 дней назад; HR и ещё три аккаунта."""
    ctx = await make_context(db_session, "ep")
    employee = await db_session.get(Employee, ctx.employee_id)
    employee.hire_date = HIRED
    employee.created_at = datetime.datetime(
        HIRED.year, HIRED.month, HIRED.day, tzinfo=datetime.UTC
    )
    own = await make_account(db_session, employee_id=employee.id, role="employee", auth_type="ldap")
    await db_session.flush()
    return ctx, employee, own


async def _account(db_session, role: str, email: str, **employee_kw):
    holder = await make_employee(db_session, email=email, **employee_kw)
    return await make_account(db_session, employee_id=holder.id, role=role, auth_type="local")


async def _events(db_session, employee_id: int) -> list[LeaveBalanceEntry]:
    result = await db_session.execute(
        select(LeaveBalanceEntry)
        .where(LeaveBalanceEntry.employee_id == employee_id)
        .order_by(LeaveBalanceEntry.id)
    )
    return list(result.scalars())


def _url(employee_id: int, suffix: str = "") -> str:
    return f"/api/employees/{employee_id}/balances{suffix}"


# --- FR-BAL-002 ----------------------------------------------------------------


async def test_fr_bal_002_opening_my_balance_gives_every_applicable_type_and_the_history(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)

    response = await api_client.get(_url(employee.id), headers=bearer_header(own))

    assert response.status_code == 200
    body = response.json()
    by_code = {item["code"]: item for item in body["balances"]}
    # Применимый вид: активный и полностью оплачиваемый (ВЫБРАНО, T-M2-11).
    assert set(by_code) == {"annual_paid", "study", "time_off", "business_trip"}
    main = by_code["annual_paid"]
    assert Decimal(main["balance"]) > 0
    assert Decimal(main["total_balance"]) == Decimal(main["balance"])
    assert main["unit"] == "calendar_days"
    assert main["working_year_start"] == HIRED.isoformat()
    assert [e["entry_type"] for e in main["history"]] == [ACCRUAL]
    assert main["history"][0]["actor_name"] is None  # начисляет система
    # Вид без событий показывается нулём; единица берётся из справочника.
    assert Decimal(by_code["study"]["balance"]) == 0 and by_code["study"]["history"] == []
    assert by_code["time_off"]["unit"] == "working_days"
    assert body["as_of"] == TODAY.isoformat()


async def test_fr_bal_002_the_history_shows_the_name_of_the_author(db_session, api_client) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-name@company.local", full_name="Анна Кадрова")
    adjust = await api_client.post(
        _url(employee.id, "/adjust"),
        json={"leave_type_id": ctx.leave_type_id, "quantity": "2", "reason": "Перенос"},
        headers=bearer_header(hr),
    )
    assert adjust.status_code == 200

    response = await api_client.get(_url(employee.id), headers=bearer_header(own))

    main = next(b for b in response.json()["balances"] if b["code"] == "annual_paid")
    manual = [e for e in main["history"] if e["entry_type"] == MANUAL_ADJUSTMENT]
    assert [(e["actor_name"], e["reason"]) for e in manual] == [("Анна Кадрова", "Перенос")]


# --- ленивое начисление (T-M2-19) ----------------------------------------------


async def test_lazy_the_first_get_writes_the_accrual_and_the_second_get_writes_nothing(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    assert await _events(db_session, employee.id) == []

    first = await api_client.get(_url(employee.id), headers=bearer_header(own))
    after_first = await _events(db_session, employee.id)
    second = await api_client.get(_url(employee.id), headers=bearer_header(own))

    assert first.status_code == second.status_code == 200
    assert len(after_first) == 1 and after_first[0].entry_type == ACCRUAL
    assert len(await _events(db_session, employee.id)) == 1  # повтор запроса безопасен
    assert first.json() == second.json()


async def test_lazy_a_get_on_a_past_date_does_not_accrue_forward(db_session, api_client) -> None:
    ctx, employee, own = await _setup(db_session)
    past = HIRED + datetime.timedelta(days=60)

    response = await api_client.get(
        _url(employee.id), params={"as_of": past.isoformat()}, headers=bearer_header(own)
    )

    assert response.status_code == 200
    events = await _events(db_session, employee.id)
    assert events and max(e.entry_date for e in events) == past  # дальше past не начислено


async def test_lazy_a_get_on_a_future_date_never_writes_an_accrual_dated_in_the_future(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    future = TODAY + datetime.timedelta(days=100)

    response = await api_client.get(
        _url(employee.id), params={"as_of": future.isoformat()}, headers=bearer_header(own)
    )

    assert response.status_code == 200 and response.json()["as_of"] == future.isoformat()
    assert max(e.entry_date for e in await _events(db_session, employee.id)) <= TODAY


async def test_lazy_an_employee_whose_every_year_is_closed_reads_without_an_error(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    db_session.add(
        LeaveBalanceEntry(
            employee_id=employee.id,
            leave_type_id=ctx.leave_type_id,
            working_year_start=HIRED,
            working_year_end=HIRED.replace(year=HIRED.year + 1),
            entry_type=INITIAL_IMPORT,
            quantity=Decimal("7"),
            entry_date=TODAY,
            actor_account_id=own.id,
        )
    )
    await db_session.flush()

    response = await api_client.get(_url(employee.id), headers=bearer_header(own))

    assert response.status_code == 200
    main = next(b for b in response.json()["balances"] if b["code"] == "annual_paid")
    assert Decimal(main["balance"]) == Decimal("7")  # начисления нет, только импорт


async def test_lazy_a_missing_main_leave_type_is_not_swallowed_by_the_endpoint(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    await db_session.execute(text("DELETE FROM leave_types WHERE code = 'annual_paid'"))

    with pytest.raises(LeaveTypeNotSeededError):
        await api_client.get(_url(employee.id), headers=bearer_header(own))


async def test_a_get_for_a_missing_employee_is_404(db_session, api_client) -> None:
    hr = await _account(db_session, "hr_admin", "hr-404@company.local")

    response = await api_client.get(_url(10_000_000), headers=bearer_header(hr))

    assert response.status_code == 404


# --- FR-BAL-004 на уровне эндпоинта -----------------------------------------------


async def test_fr_bal_004_a_request_without_a_reason_is_rejected_by_the_schema(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-noreason@company.local")

    response = await api_client.post(
        _url(employee.id, "/adjust"),
        json={"leave_type_id": ctx.leave_type_id, "quantity": "2"},
        headers=bearer_header(hr),
    )

    assert response.status_code == 422


@pytest.mark.parametrize("reason", ["", "   ", " \t"])
async def test_fr_bal_004_a_blank_reason_is_rejected_by_the_service(
    db_session, api_client, reason
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", f"hr-blank{len(reason)}@company.local")

    response = await api_client.post(
        _url(employee.id, "/adjust"),
        json={"leave_type_id": ctx.leave_type_id, "quantity": "2", "reason": reason},
        headers=bearer_header(hr),
    )

    assert response.status_code == 400
    events = await _events(db_session, employee.id)
    assert [e for e in events if e.entry_type == MANUAL_ADJUSTMENT] == []


async def test_fr_bal_004_an_adjustment_writes_one_event_with_the_author_and_the_working_year(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-adjust@company.local")
    entry_date = HIRED + datetime.timedelta(days=30)

    response = await api_client.post(
        _url(employee.id, "/adjust"),
        json={
            "leave_type_id": ctx.leave_type_id,
            "quantity": "-1.5",
            "reason": "  Ошибка переноса  ",
            "entry_date": entry_date.isoformat(),
        },
        headers=bearer_header(hr),
    )

    assert response.status_code == 200
    events = await _events(db_session, employee.id)
    manual = [e for e in events if e.entry_type == MANUAL_ADJUSTMENT]
    assert len(manual) == 1
    (event,) = manual
    assert (event.actor_account_id, event.reason, event.quantity) == (
        hr.id,
        "Ошибка переноса",
        Decimal("-1.5"),
    )
    # Рабочий год определил модуль balances, клиент его не присылал.
    body = response.json()
    assert body["working_year_start"] == event.working_year_start.isoformat() == HIRED.isoformat()
    assert event.working_year_start <= entry_date < event.working_year_end


async def test_fr_bal_004_an_adjustment_is_preceded_by_the_lazy_accrual(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-lazy@company.local")

    response = await api_client.post(
        _url(employee.id, "/adjust"),
        json={"leave_type_id": ctx.leave_type_id, "quantity": "1", "reason": "Добавка"},
        headers=bearer_header(hr),
    )

    types = [e.entry_type for e in await _events(db_session, employee.id)]
    assert types == [ACCRUAL, MANUAL_ADJUSTMENT]
    accrued = (await _events(db_session, employee.id))[0].quantity
    # Возвращённый остаток включает начисление.
    assert Decimal(response.json()["balance"]) == accrued + 1


async def test_fr_bal_004_adjustment_before_the_hire_date_unknown_type_and_zero_are_refused(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-refuse@company.local")
    headers = bearer_header(hr)
    base = {"leave_type_id": ctx.leave_type_id, "quantity": "1", "reason": "Причина"}

    before_hire = await api_client.post(
        _url(employee.id, "/adjust"),
        json={**base, "entry_date": (HIRED - datetime.timedelta(days=1)).isoformat()},
        headers=headers,
    )
    unknown_type = await api_client.post(
        _url(employee.id, "/adjust"), json={**base, "leave_type_id": 10_000_000}, headers=headers
    )
    unknown_employee = await api_client.post(
        _url(10_000_000, "/adjust"), json=base, headers=headers
    )
    zero = await api_client.post(
        _url(employee.id, "/adjust"), json={**base, "quantity": "0"}, headers=headers
    )

    assert (before_hire.status_code, unknown_type.status_code) == (400, 404)
    assert (unknown_employee.status_code, zero.status_code) == (404, 400)


# --- доступ по матрице раздела 7 ---------------------------------------------------


async def test_access_the_employee_reads_the_own_balance_by_the_linked_card_not_the_role(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)

    assert (await api_client.get(_url(employee.id), headers=bearer_header(own))).status_code == 200


async def test_access_a_manager_reads_the_balance_of_a_subordinate(db_session, api_client) -> None:
    ctx, employee, own = await _setup(db_session)
    manager = await _account(db_session, "employee", "boss@company.local")
    employee.manager_id = manager.employee_id
    await db_session.flush()

    response = await api_client.get(_url(employee.id), headers=bearer_header(manager))

    assert response.status_code == 200


async def test_access_a_manager_cannot_read_the_balance_of_a_stranger(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    stranger_boss = await _account(db_session, "employee", "not-boss@company.local")

    response = await api_client.get(_url(employee.id), headers=bearer_header(stranger_boss))

    assert response.status_code == 403
    assert await _events(db_session, employee.id) == []  # отказ до начисления


async def test_access_the_accountant_reads_but_cannot_adjust(db_session, api_client) -> None:
    ctx, employee, own = await _setup(db_session)
    accountant = await _account(db_session, "accountant", "acc@company.local")
    headers = bearer_header(accountant)

    read = await api_client.get(_url(employee.id), headers=headers)
    adjust = await api_client.post(
        _url(employee.id, "/adjust"),
        json={"leave_type_id": ctx.leave_type_id, "quantity": "1", "reason": "Причина"},
        headers=headers,
    )

    assert (read.status_code, adjust.status_code) == (200, 403)


async def test_access_hr_reads_and_adjusts_system_admin_reads_only(db_session, api_client) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-both@company.local")
    admin = await _account(db_session, "system_admin", "sysadmin@company.local")
    payload = {"leave_type_id": ctx.leave_type_id, "quantity": "1", "reason": "Причина"}

    hr_read = await api_client.get(_url(employee.id), headers=bearer_header(hr))
    hr_adjust = await api_client.post(
        _url(employee.id, "/adjust"), json=payload, headers=bearer_header(hr)
    )
    admin_read = await api_client.get(_url(employee.id), headers=bearer_header(admin))
    admin_adjust = await api_client.post(
        _url(employee.id, "/adjust"), json=payload, headers=bearer_header(admin)
    )

    assert (hr_read.status_code, hr_adjust.status_code) == (200, 200)
    assert (admin_read.status_code, admin_adjust.status_code) == (200, 403)


async def test_access_an_unauthenticated_request_is_refused(db_session, api_client) -> None:
    ctx, employee, own = await _setup(db_session)

    assert (await api_client.get(_url(employee.id))).status_code == 401
    assert (await api_client.post(_url(employee.id, "/adjust"), json={})).status_code == 401


async def test_the_total_balance_sums_every_working_year(db_session, api_client) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-total@company.local")
    employee.hire_date = HIRED - datetime.timedelta(days=400)  # завершённый год + текущий
    employee.created_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    await db_session.flush()

    response = await api_client.get(_url(employee.id), headers=bearer_header(hr))

    main = next(b for b in response.json()["balances"] if b["code"] == "annual_paid")
    years = {e["working_year_start"] for e in main["history"]}
    assert len(years) == 2
    assert Decimal(main["total_balance"]) > Decimal(main["balance"])
