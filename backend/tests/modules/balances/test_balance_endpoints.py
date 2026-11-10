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
from app.modules.balances.accrual import accrual_steps, compute_accrual, working_year_starts
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


def _step_dates(hire: datetime.date, up_to: datetime.date) -> list[datetime.date]:
    """Даты шагов начисления всех рабочих годов с приёма до `up_to` (T-M2-21): по событию
    на каждый, норма и порог - из сидирования (28 и 15). Завершённые годы целиком,
    текущий - до `up_to`."""
    return [
        step.date
        for start in working_year_starts(hire, up_to)
        for step in accrual_steps(
            hire_date=hire,
            working_year_start=start,
            as_of=up_to,
            annual_norm=28,
            rounding_threshold_days=15,
            excluded_days=0,
        )
    ]


def _url(employee_id: int, suffix: str = "") -> str:
    return f"/api/employees/{employee_id}/balances{suffix}"


# --- FR-BAL-002 ----------------------------------------------------------------


async def test_fr_bal_002_opening_my_balance_gives_the_main_type_with_its_history(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)

    response = await api_client.get(_url(employee.id), headers=bearer_header(own))

    assert response.status_code == 200
    body = response.json()
    by_code = {item["code"]: item for item in body["balances"]}
    # Набор видов (T-M2-20, требования v1.26): активные виды с признаком «по виду ведётся
    # остаток» (основной и отгул) + виды с событиями. Без признака (sick, study,
    # business_trip, unpaid, maternity_childcare) и неактивный additional_paid не показываются.
    assert set(by_code) == {"annual_paid", "time_off"}
    assert Decimal(by_code["time_off"]["balance"]) == 0  # осмысленный ноль: компенсации нет
    assert by_code["time_off"]["history"] == []
    assert by_code["time_off"]["unit"] == "working_days"
    main = by_code["annual_paid"]
    assert Decimal(main["balance"]) > 0
    assert Decimal(main["total_balance"]) == Decimal(main["balance"])
    assert main["unit"] == "calendar_days"
    assert main["working_year_start"] == HIRED.isoformat()
    # T-M2-21: по строке на шаг накопления (200 дней - шесть-семь шагов), не одна на год
    assert len(main["history"]) == len(_step_dates(HIRED, TODAY)) > 1
    assert {e["entry_type"] for e in main["history"]} == {ACCRUAL}
    assert {e["actor_name"] for e in main["history"]} == {"Система"}  # начисляет система
    assert "actor_account_id" not in main["history"][0]  # внутренний идентификатор не отдаётся
    assert body["as_of"] == TODAY.isoformat()


async def test_fr_bal_002_an_untracked_type_with_events_still_appears_with_its_unit(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-types@company.local")
    study_id = (
        await db_session.execute(text("SELECT id FROM leave_types WHERE code = 'study'"))
    ).scalar_one()
    adjust = await api_client.post(
        _url(employee.id, "/adjust"),
        json={"leave_type_id": study_id, "quantity": "8", "reason": "Запись по виду без признака"},
        headers=bearer_header(hr),
    )
    assert adjust.status_code == 200

    response = await api_client.get(_url(employee.id), headers=bearer_header(own))

    by_code = {item["code"]: item for item in response.json()["balances"]}
    # study без признака, но с событием: история не прячется; sick, business_trip - без событий
    assert set(by_code) == {"annual_paid", "time_off", "study"}
    assert by_code["study"]["unit"] == "calendar_days"  # единица из справочника
    assert Decimal(by_code["study"]["balance"]) == Decimal("8")


async def test_fr_bal_002_an_inactive_tracked_type_is_hidden_until_it_has_events(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    additional_id = (
        await db_session.execute(
            text("SELECT id FROM leave_types WHERE code = 'additional_paid'")
        )
    ).scalar_one()

    hr = await _account(db_session, "hr_admin", "hr-inactive@company.local")
    hidden = await api_client.get(_url(employee.id), headers=bearer_header(own))
    # Вид с событиями показывается, даже когда он неактивен: событие пишем напрямую.
    db_session.add(
        LeaveBalanceEntry(
            employee_id=employee.id,
            leave_type_id=additional_id,
            entry_type=MANUAL_ADJUSTMENT,
            quantity=Decimal("2"),
            entry_date=TODAY,
            working_year_start=HIRED,
            working_year_end=HIRED.replace(year=HIRED.year + 1),
            reason="Исторический остаток неактивного вида",
            actor_account_id=hr.id,
        )
    )
    await db_session.flush()
    shown = await api_client.get(_url(employee.id), headers=bearer_header(own))

    assert "additional_paid" not in {b["code"] for b in hidden.json()["balances"]}
    assert "additional_paid" in {b["code"] for b in shown.json()["balances"]}


async def test_fr_bal_002_the_history_is_cut_at_the_requested_date(db_session, api_client) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-cut@company.local")
    adjust_date = HIRED + datetime.timedelta(days=100)
    await api_client.post(
        _url(employee.id, "/adjust"),
        json={
            "leave_type_id": ctx.leave_type_id,
            "quantity": "3",
            "reason": "Позже",
            "entry_date": adjust_date.isoformat(),
        },
        headers=bearer_header(hr),
    )

    earlier = await api_client.get(
        _url(employee.id),
        params={"as_of": (adjust_date - datetime.timedelta(days=1)).isoformat()},
        headers=bearer_header(own),
    )

    main = earlier.json()["balances"][0]
    assert MANUAL_ADJUSTMENT not in [e["entry_type"] for e in main["history"]]


async def test_fr_bal_002_the_history_shows_the_name_of_the_author_not_the_login(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-name@company.local", full_name="Анна Кадрова")
    # Аккаунт БЕЗ карточки (например, первичный администратор): логин не показывается.
    cardless = await make_account(
        db_session, login="secret-boss", role="hr_admin", auth_type="local"
    )
    for account, quantity in ((hr, "2"), (cardless, "1")):
        adjust = await api_client.post(
            _url(employee.id, "/adjust"),
            json={"leave_type_id": ctx.leave_type_id, "quantity": quantity, "reason": "Перенос"},
            headers=bearer_header(account),
        )
        assert adjust.status_code == 200

    response = await api_client.get(_url(employee.id), headers=bearer_header(own))

    main = next(b for b in response.json()["balances"] if b["code"] == "annual_paid")
    manual = [e for e in main["history"] if e["entry_type"] == MANUAL_ADJUSTMENT]
    assert [e["actor_name"] for e in manual] == ["Анна Кадрова", "Администратор"]
    assert "secret-boss" not in response.text


async def test_fr_bal_002_the_history_is_cut_at_the_requested_date_and_keeps_the_accrual(
    db_session, api_client
) -> None:
    # Принят 400 дней назад: завершённый год начислен на его последний день.
    ctx, employee, own = await _setup(db_session)
    hire = TODAY - datetime.timedelta(days=400)
    employee.hire_date = hire
    employee.created_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    await db_session.flush()
    hr = await _account(db_session, "hr_admin", "hr-cut@company.local")
    first_year_last_day = hire.replace(year=hire.year + 1) - datetime.timedelta(days=1)
    adjust_date = first_year_last_day + datetime.timedelta(days=5)
    adjust = await api_client.post(
        _url(employee.id, "/adjust"),
        json={
            "leave_type_id": ctx.leave_type_id,
            "quantity": "3",
            "reason": "Позже",
            "entry_date": adjust_date.isoformat(),
        },
        headers=bearer_header(hr),
    )
    assert adjust.status_code == 200

    earlier = await api_client.get(
        _url(employee.id),
        params={"as_of": (adjust_date - datetime.timedelta(days=1)).isoformat()},
        headers=bearer_header(own),
    )

    main = earlier.json()["balances"][0]
    types = {e["entry_type"] for e in main["history"]}
    assert types == {ACCRUAL}  # начисления за завершённый год остались, корректировки нет
    # Ожидание - из `accrual_steps`: равенство этой арифметики с `compute_accrual` закреплено
    # перебором дней (`test_accrual_arithmetic.py`, `test_accrual_step_dates.py`). Здесь
    # проверяется, что эндпоинт отдаёт ИМЕННО её даты, все двенадцать точно; верность самих дат
    # этот тест не проверяет - при ошибке в `accrual_steps` он остался бы зелёным.
    expected = [
        step.date.isoformat()
        for step in accrual_steps(
            hire_date=hire,
            working_year_start=hire,
            as_of=first_year_last_day,
            annual_norm=28,
            rounding_threshold_days=15,
            excluded_days=0,
        )
    ]
    assert len(expected) == 12
    assert sorted(e["entry_date"] for e in main["history"]) == expected


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
    assert {e.entry_type for e in after_first} == {ACCRUAL}
    assert len(after_first) == len(_step_dates(HIRED, TODAY))  # T-M2-21: по событию на шаг
    assert len(await _events(db_session, employee.id)) == len(after_first)  # повтор безопасен
    assert first.json() == second.json()


async def test_lazy_a_get_on_a_past_date_does_not_write_events_dated_that_day(
    db_session, api_client
) -> None:
    # Смотреть остаток на прошлую дату - не повод менять журнал задним числом: начисление
    # идёт до СЕГОДНЯ, `as_of` - только фильтр показа.
    ctx, employee, own = await _setup(db_session)
    hire = TODAY - datetime.timedelta(days=400)
    employee.hire_date = hire
    employee.created_at = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    await db_session.flush()
    first_year_last_day = hire.replace(year=hire.year + 1) - datetime.timedelta(days=1)
    past = hire + datetime.timedelta(days=50)

    on_past = await api_client.get(
        _url(employee.id), params={"as_of": past.isoformat()}, headers=bearer_header(own)
    )
    after_past = await _events(db_session, employee.id)
    today = await api_client.get(_url(employee.id), headers=bearer_header(own))

    assert on_past.status_code == today.status_code == 200
    # События датированы днями шагов (T-M2-21), а не `past` и не днём запроса.
    assert sorted(e.entry_date for e in after_past) == _step_dates(hire, TODAY)
    assert len(await _events(db_session, employee.id)) == len(after_past)  # «сегодня» не дописало
    # Остаток на прошлую дату - записанное на неё, и он равен расчёту на эту дату.
    assert Decimal(on_past.json()["balances"][0]["balance"]) == compute_accrual(
        hire_date=hire,
        as_of=past,
        annual_norm=28,
        rounding_threshold_days=15,
        excluded_days=0,
    ).accrued
    at_year_end = await api_client.get(
        _url(employee.id),
        params={"as_of": first_year_last_day.isoformat()},
        headers=bearer_header(own),
    )
    assert Decimal(at_year_end.json()["balances"][0]["balance"]) == Decimal("28.00")


async def test_lazy_a_get_on_a_future_date_never_writes_an_accrual_dated_in_the_future(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    future = TODAY + datetime.timedelta(days=100)

    response = await api_client.get(
        _url(employee.id), params={"as_of": future.isoformat()}, headers=bearer_header(own)
    )

    assert response.status_code == 200 and response.json()["as_of"] == future.isoformat()
    events = await _events(db_session, employee.id)
    assert events  # начисление до сегодня записано
    # Шаги - до сегодня; ни одного, датированного будущим (`future` просили, а не начисляли).
    assert sorted(e.entry_date for e in events) == _step_dates(HIRED, TODAY)


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

    events = await _events(db_session, employee.id)
    steps = len(_step_dates(HIRED, TODAY))
    assert [e.entry_type for e in events] == [ACCRUAL] * steps + [MANUAL_ADJUSTMENT]
    accrued = sum(e.quantity for e in events if e.entry_type == ACCRUAL)
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


# --- уволенный, неактивный вид, границы корректировки ---------------------------


async def test_a_terminated_employee_is_accrued_only_up_to_the_termination_date(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    termination = HIRED + datetime.timedelta(days=100)
    employee.employment_status = "terminated"
    employee.termination_date = termination
    await db_session.flush()
    hr = await _account(db_session, "hr_admin", "hr-term@company.local")

    response = await api_client.get(_url(employee.id), headers=bearer_header(hr))

    assert response.status_code == 200
    events = await _events(db_session, employee.id)
    # Шаги до даты увольнения включительно (T-M2-21); после неё - ничего.
    assert events and sorted(e.entry_date for e in events) == _step_dates(HIRED, termination)
    assert max(e.entry_date for e in events) <= termination


async def test_a_manager_of_a_subordinates_subordinate_reads_the_balance(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    middle = await _account(db_session, "employee", "middle@company.local")
    top = await _account(db_session, "employee", "top@company.local")
    employee.manager_id = middle.employee_id
    middle_employee = await db_session.get(Employee, middle.employee_id)
    middle_employee.manager_id = top.employee_id
    await db_session.flush()

    response = await api_client.get(_url(employee.id), headers=bearer_header(top))

    assert response.status_code == 200


async def test_fr_bal_004_an_adjustment_for_a_missing_employee_is_404(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-missing@company.local")

    response = await api_client.post(
        _url(10_000_000, "/adjust"),
        json={"leave_type_id": ctx.leave_type_id, "quantity": "1", "reason": "Причина"},
        headers=bearer_header(hr),
    )

    assert response.status_code == 404


async def test_fr_bal_004_an_adjustment_dated_in_the_future_is_rejected_by_the_schema(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-future@company.local")

    response = await api_client.post(
        _url(employee.id, "/adjust"),
        json={
            "leave_type_id": ctx.leave_type_id,
            "quantity": "1",
            "reason": "Причина",
            "entry_date": (TODAY + datetime.timedelta(days=1)).isoformat(),
        },
        headers=bearer_header(hr),
    )

    assert response.status_code == 422
    events = await _events(db_session, employee.id)
    assert [e for e in events if e.entry_type == MANUAL_ADJUSTMENT] == []


async def test_fr_bal_004_an_adjustment_for_an_inactive_leave_type_is_refused(
    db_session, api_client
) -> None:
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-inactive@company.local")
    inactive_id = (
        await db_session.execute(text("SELECT id FROM leave_types WHERE code = 'additional_paid'"))
    ).scalar_one()  # засеян неактивным (A-3.1)

    response = await api_client.post(
        _url(employee.id, "/adjust"),
        json={"leave_type_id": inactive_id, "quantity": "1", "reason": "Причина"},
        headers=bearer_header(hr),
    )

    assert response.status_code == 400
    assert "неактивен" in response.json()["detail"]


async def test_an_inactive_main_leave_type_is_still_shown_but_cannot_be_adjusted(
    db_session, api_client
) -> None:
    # Остаток существует независимо от того, активен ли справочный вид: деактивация вида
    # не должна прятать накопленное (ВЫБРАНО); корректировать по неактивному нельзя.
    ctx, employee, own = await _setup(db_session)
    hr = await _account(db_session, "hr_admin", "hr-inactive-main@company.local")
    await db_session.execute(
        text("UPDATE leave_types SET is_active = false WHERE code = 'annual_paid'")
    )

    read = await api_client.get(_url(employee.id), headers=bearer_header(own))
    adjust = await api_client.post(
        _url(employee.id, "/adjust"),
        json={"leave_type_id": ctx.leave_type_id, "quantity": "1", "reason": "Причина"},
        headers=bearer_header(hr),
    )

    assert read.status_code == 200
    # Неактивный основной виден по событию (ленивое начисление его записало), отгул - по признаку.
    assert [b["code"] for b in read.json()["balances"]] == ["annual_paid", "time_off"]
    assert adjust.status_code == 400
